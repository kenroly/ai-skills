---
name: gen-anh
description: 'API-only image generation and editing. Do not use ChatGPT/Codex subscription image generation or subscription entitlements for this skill. Use when the user asks to gen ảnh, tạo ảnh, vẽ ảnh, sửa ảnh, chỉnh sửa ảnh, đổi màu, use an image reference, generate an image, or edit an existing image and an API-backed image workflow is intended. Supports illustrations, product images, banners, concept art, and raster assets.'
---

# Gen ảnh

Use this skill only for API-backed image generation or editing. Do not route
requests through ChatGPT/Codex subscription image generation, subscription
entitlements, or non-API UI workflows.

Generate real images with HTTP `POST /v1/responses`, native `image_generation`, and the user's existing local Sub2API gateway. The absence of an exposed `image_gen` tool does not establish that this route is unavailable. Use the included helper rather than the bundled `/images/generations` CLI fallback for this workflow.

## Generate and deliver

1. Turn the user's request into a concise image prompt. For an unspecified test, choose a harmless creative subject and proceed. Preserve explicit style, text, composition, and aspect ratio requirements.
   For an existing image, inspect it first and identify whether it is an edit target or a style/content reference. State which details to change and which to preserve; pass the actual image bytes with `--image` instead of merely describing it.
2. Run `scripts/generate.py` with Python 3.11+ and Pillow. It reads `$CODEX_HOME/config.toml` and `auth.json` (default `~/.codex`), uses the top-level configured model/provider, and sends a native image tool request through the configured loopback gateway. It does not require a separate OpenAI platform key. `SUB2API_API_KEY`, if set, overrides the saved gateway key.
3. Inspect the returned file with `view_image`. A successful request must yield a decodable final image, not merely HTTP 200, an injected-tool log, a partial preview, or assistant prose.
4. Render the image inline using an absolute path. Briefly report the result and link the file when helpful. The sibling JSON records the prompt, returned image properties, response status, and timing without credentials or base64.

Example on this machine:

```powershell
python C:\Users\TheDat\.codex\skills\gen-anh\scripts\generate.py --prompt 'Generate a square illustration of an orange cat astronaut on the Moon, Earth in the sky, polished 3D animation style, no text.' --out C:\Users\TheDat\.codex\generated_images\astronaut-cat-v2.png
```

For multiline prompts, write UTF-8 text and pass `--prompt-file ABSOLUTE_PATH`. Quote paths with spaces. `--model` explicitly overrides the top-level Responses model. The script requests PNG; existing image or metadata files cause an error before any network request. Use a new filename for variants.

Preview-only images may stay in `$CODEX_HOME/generated_images`. For project assets, choose a path inside that project and update the consuming code when requested. Put visual constraints in the prompt; inspect actual returned dimensions instead of assuming they match a requested size.

## Reference images and edits

Use `--image ABSOLUTE_PATH --action edit` to edit an existing image. For a new composition based on references, use `--image ABSOLUTE_PATH --action generate`; `--action auto` lets the native tool choose. Inputs are sent as base64 data URLs in Responses `input_image` content. Static PNG, JPEG, WebP, and GIF files are accepted and checked before sending.

```powershell
python C:\Users\TheDat\.codex\skills\gen-anh\scripts\generate.py --image C:\path\original.png --action edit --prompt 'Edit Image 1: change the white spacesuit fabric to emerald green. Preserve the cat, pose, helmet, rover, background, framing and lighting.' --out C:\path\edited-green.png
```

Repeat `--image` for multiple inputs, numbered Image 1, Image 2, etc. in argument order. Explain their roles in the prompt, for example "Image 1 is the edit target; use Image 2 only as a color reference." Multiple-image packaging is locally checked; a live multi-reference generation has not yet been tested. Single-image editing was verified on 2026-09-24 using `gpt-6-sol`: a white astronaut suit changed to green, with the character and overall composition closely preserved. The response explicitly reported `action: edit`, producing a 1254x1254 PNG in about 67 seconds.

Always save edits to a new file. For a follow-up edit, pass the selected previous output as `--image`; this workflow does not require server-side conversation storage. Inspect the result against the input and report any visible drift. Preservation is a model instruction, not a guarantee of identical untouched pixels. Metadata includes the ordered input paths and SHA-256 hashes, never embedded image bytes.

## Operational facts and limits

- The local gateway tested on 2026-09-22 was `http://127.0.0.1:8092/v1`; read current configuration instead of hardcoding it. The helper intentionally accepts only loopback HTTP/HTTPS endpoints and refuses redirects to protect saved credentials.
- The request explicitly supplies `tools: [{type: image_generation, output_format: png}]`, `tool_choice: {type: image_generation}`, `stream: true`, and `store: false`. It uses Codex client headers. This makes native tool selection explicit; it does not depend on a hidden tool appearing in the agent's tool list.
- The top-level model must be Responses-capable, not an image-only model. The verified run used `gpt-6-astra`. Do not claim that this proves a particular underlying GPT Image model: the response did not identify one.
- Local bridge flags observed: `GATEWAY_CODEX_IMAGE_GENERATION_BRIDGE_ENABLED=true`, `GATEWAY_FORCE_CODEX_CLI=true`, `GATEWAY_OPENAI_WS_ENABLED=false`, `GATEWAY_OPENAI_WS_RESPONSES_WEBSOCKETS_V2=false`, and `GATEWAY_OPENAI_WS_FORCE_HTTP=true`. Routing needs an eligible OAuth account and a group allowing image generation. Account/channel overrides can affect bridge behavior.
- The helper handles both final `response.output_item.done` image items and `response.completed.output`, deduplicates them, and verifies PNG decoding with Pillow. It ignores partial images. It performs no automatic retries: after a timeout or interrupted stream, inspect any saved output before deciding whether another generation is warranted.
- Missing credentials, group denial, unavailable models, or gateway errors should be reported precisely. Do not print auth files, tokens, Authorization headers, or full base64 responses. Do not silently change global Codex configuration, gateway flags, group permissions, accounts, or the requested model.
- Transparency, masking, and exact size/quality controls have not been validated by this skill. Do not promise those based solely on the successful generation and single-image editing tests.

For troubleshooting only, the local deployment is `E:\Code\asisstant\local-sub2api`, container `sub2api-local`; logs are under `data/logs/sub2api.log`. Source inspected at `E:\Code\asisstant\_tmp_sub2api\backend\internal\service\openai_codex_transform.go` shows tool injection and bridge instructions; a source snapshot may differ from the running image. Native Responses result format is documented at https://developers.openai.com/api/docs/guides/tools-image-generation.
