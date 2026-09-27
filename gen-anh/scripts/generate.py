#!/usr/bin/env python3
"""Generate or edit a PNG using text and optional reference images."""

import argparse
import base64
import io
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request

from PIL import Image


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError('Gateway redirect refused; verify the configured base URL.')


def read_configuration(model_override=None):
    codex_dir = Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex')
    config = tomllib.loads((codex_dir / 'config.toml').read_text(encoding='utf-8-sig'))
    provider = config.get('model_providers', {}).get(config.get('model_provider'), {})
    base_url = provider.get('base_url', '').rstrip('/')
    parsed = urllib.parse.urlsplit(base_url)
    try:
        local = parsed.hostname == 'localhost' or ipaddress.ip_address(parsed.hostname or '').is_loopback
    except ValueError:
        local = False
    if (parsed.scheme not in ('http', 'https') or not local or parsed.username
            or parsed.password or parsed.query or parsed.fragment):
        raise ValueError('Configured provider must be the local Sub2API HTTP/HTTPS gateway.')
    key = os.environ.get('SUB2API_API_KEY')
    if not key and provider.get('env_key'):
        key = os.environ.get(provider['env_key'])
        if not key:
            raise ValueError('The configured provider env_key is not set.')
    if not key:
        key = json.loads((codex_dir / 'auth.json').read_text(encoding='utf-8-sig')).get('OPENAI_API_KEY')
    if not key:
        raise ValueError('No gateway key found in SUB2API_API_KEY or Codex authentication.')
    model = model_override or config.get('model')
    if not model:
        raise ValueError('No Responses model configured; supply --model.')
    return base_url, key, model


def sse_events(response):
    if 'application/json' in response.headers.get('Content-Type', ''):
        result = json.load(response)
        yield {'type': 'response.' + result.get('status', 'unknown'), 'response': result}
        return
    lines = []
    for raw in response:
        line = raw.decode('utf-8').rstrip('\r\n')
        if line.startswith('data:'):
            lines.append(line[5:].lstrip(' '))
        elif not line and lines:
            data = '\n'.join(lines)
            lines = []
            if data == '[DONE]':
                return
            yield json.loads(data)
    if lines and '\n'.join(lines) != '[DONE]':
        yield json.loads('\n'.join(lines))


def image_content(image_paths):
    content, references = [], []
    for index, value in enumerate(image_paths, 1):
        path = Path(value).expanduser().resolve()
        data = path.read_bytes()
        with Image.open(io.BytesIO(data)) as img:
            mime = {'PNG': 'image/png', 'JPEG': 'image/jpeg', 'WEBP': 'image/webp',
                    'GIF': 'image/gif'}.get(img.format)
            if not mime or getattr(img, 'is_animated', False):
                raise ValueError('Reference images must be static PNG, JPEG, WebP, or GIF files.')
            dimensions = list(img.size)
            img.verify()
        with Image.open(io.BytesIO(data)) as img:
            img.load()
        content.extend([
            {'type': 'input_text', 'text': 'Image ' + str(index) + ':'},
            {'type': 'input_image', 'image_url': 'data:' + mime + ';base64,' +
             base64.b64encode(data).decode('ascii')},
        ])
        references.append({'index': index, 'path': str(path), 'dimensions': dimensions,
                           'sha256': hashlib.sha256(data).hexdigest()})
    return content, references


def generate(prompt, output, model_override=None, timeout=240, images=(), action=None):
    output = output.expanduser().resolve()
    metadata_path = output.with_suffix('.json')
    if output.suffix.lower() != '.png':
        raise ValueError('--out must name a .png file.')
    if output.exists() or metadata_path.exists():
        raise FileExistsError('Output or metadata already exists; choose a new filename.')
    if not prompt.strip():
        raise ValueError('Prompt is empty.')
    if action not in (None, 'auto', 'generate', 'edit'):
        raise ValueError('Action must be auto, generate, or edit.')
    if action == 'edit' and not images:
        raise ValueError('--action edit requires at least one --image.')
    references_content, references = image_content(images)
    base_url, key, model = read_configuration(model_override)
    tool = {'type': 'image_generation', 'output_format': 'png'}
    if action:
        tool['action'] = action
    payload = {
        'model': model,
        'instructions': 'Generate or edit the requested image using the native image_generation tool. Follow the user instructions about which image details to change and preserve.',
        'input': [{'role': 'user', 'content': [{'type': 'input_text', 'text': prompt}] + references_content}],
        'tools': [tool],
        'tool_choice': {'type': 'image_generation'},
        'stream': True,
        'store': False,
    }
    request = urllib.request.Request(
        base_url + '/responses', data=json.dumps(payload).encode('utf-8'),
        headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json',
                 'Accept': 'text/event-stream', 'User-Agent': 'codex_cli_rs/0.115.0',
                 'originator': 'codex_cli_rs'},
    )
    started = time.monotonic()
    saved = None
    terminal_status = None
    response_id = None
    seen_events = set()

    def save_item(item):
        nonlocal saved
        if saved or item.get('type') != 'image_generation_call' or not item.get('result'):
            return
        if item.get('status') not in (None, 'completed'):
            return
        data = base64.b64decode(item['result'], validate=True)
        with Image.open(io.BytesIO(data)) as img:
            if img.format != 'PNG':
                raise ValueError('Gateway returned a non-PNG image despite the requested format.')
            dimensions = list(img.size)
            img.verify()
        with Image.open(io.BytesIO(data)) as img:
            img.load()
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open('xb') as target:
            target.write(data)
        saved = {
            'path': str(output), 'bytes': len(data), 'dimensions': dimensions,
            'prompt': prompt, 'requested_model': model, 'endpoint': base_url + '/responses',
            'references': references, 'requested_action': action,
            'image': {field: item[field] for field in (
                'id', 'status', 'action', 'background', 'output_format', 'quality',
                'size', 'revised_prompt') if field in item},
        }
        print('Saved image: ' + str(output), flush=True)

    try:
        opener = urllib.request.build_opener(NoRedirect)
        with opener.open(request, timeout=timeout) as response:
            print('HTTP ' + str(response.status), flush=True)
            for event in sse_events(response):
                if time.monotonic() - started > timeout:
                    raise TimeoutError('Generation exceeded the configured deadline.')
                kind = event.get('type', 'unknown')
                if kind not in seen_events:
                    seen_events.add(kind)
                    print(kind, flush=True)
                if kind == 'response.output_item.done':
                    save_item(event.get('item', {}))
                if kind in ('response.completed', 'response.failed', 'response.incomplete'):
                    result = event.get('response', {})
                    terminal_status = result.get('status') or kind.removeprefix('response.')
                    response_id = result.get('id')
                    for item in result.get('output', []):
                        save_item(item)
                    if terminal_status != 'completed':
                        raise RuntimeError('Response ' + terminal_status + ': ' +
                                           json.dumps(result.get('error'))[:1000])
                    break
                if kind == 'error':
                    raise RuntimeError('Gateway error: ' + json.dumps(event)[:1000])
        if terminal_status != 'completed':
            raise RuntimeError('Stream ended without response.completed; no automatic retry.')
        if not saved:
            raise RuntimeError('Response completed without a final image_generation_call result.')
    except urllib.error.HTTPError as exc:
        detail = exc.read(2000).decode('utf-8', errors='replace').replace(key, '[REDACTED]')
        raise RuntimeError('Gateway HTTP ' + str(exc.code) + ': ' + detail) from None
    except Exception as exc:
        raise RuntimeError(str(exc).replace(key, '[REDACTED]')) from None
    finally:
        if saved:
            saved.update(response_id=response_id, response_status=terminal_status or 'interrupted',
                         elapsed_seconds=round(time.monotonic() - started, 1))
            with metadata_path.open('x', encoding='utf-8') as target:
                json.dump(saved, target, indent=2, ensure_ascii=False)
    print(json.dumps(saved, ensure_ascii=False), flush=True)
    return saved


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    prompts = parser.add_mutually_exclusive_group(required=True)
    prompts.add_argument('--prompt')
    prompts.add_argument('--prompt-file', type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--model', help='Responses model; defaults to top-level Codex config model')
    parser.add_argument('--timeout', type=int, default=240)
    parser.add_argument('--image', type=Path, action='append', default=[],
                        help='Local reference or edit-target image; repeat in prompt order')
    parser.add_argument('--action', choices=['auto', 'generate', 'edit'],
                        help='Native image tool action; edit requires --image')
    args = parser.parse_args()
    try:
        if args.timeout < 1:
            raise ValueError('--timeout must be positive.')
        prompt = args.prompt if args.prompt is not None else args.prompt_file.read_text(encoding='utf-8-sig')
        generate(prompt, args.out, args.model, args.timeout, args.image, args.action)
    except Exception as exc:
        print('ERROR: ' + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
