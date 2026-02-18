"""BlockBrain REST API client — pure requests, no SDK."""

import argparse
import json
import logging
import mimetypes
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ── Constants ───────────────────────────────────────────────────────────────

POST_PROCESSING_SETTLE_SECONDS = 5
"""Seconds to wait after file processing completes, allowing indexing to commit."""


# ── Configuration ───────────────────────────────────────────────────────────

@dataclass
class BlockBrainConfig:
    """All settings required to talk to the BlockBrain REST API.

    Defaults are drawn from environment variables at import time.
    Pass explicit values to override.
    """

    api_base: str = os.getenv("BLOCKBRAIN_API_BASE", "https://blocky.theblockbrain.ai")
    api_token: str = os.getenv("BLOCKBRAIN_API_TOKEN", "")
    bot_id: str = os.getenv("BLOCKBRAIN_BOT_ID", "")
    chat_model: str = os.getenv("BLOCKBRAIN_CHAT_MODEL", "google-gemini-2.5-flash")
    image_model: str = os.getenv("BLOCKBRAIN_IMAGE_MODEL", "google-vertex-25-flash-image")
    tenant_domain: str = os.getenv("BLOCKBRAIN_TENANT_DOMAIN", "sick")


# ── Shared requests.Session ─────────────────────────────────────────────────

def _build_session(cfg: BlockBrainConfig) -> requests.Session:
    """Create a requests.Session pre-configured with auth + tenant headers."""
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {cfg.api_token}",
        "Content-Type": "application/json",
    })
    if cfg.tenant_domain:
        origin = f"https://{cfg.tenant_domain}.kb.theblockbrain.ai"
        session.headers["Referer"] = f"{origin}/"
        session.headers["Origin"] = origin
    return session


# ── REST helpers ────────────────────────────────────────────────────────────

def _url(cfg: BlockBrainConfig, path: str) -> str:
    """Build a full URL from a relative API path."""
    return f"{cfg.api_base.rstrip('/')}{path}"


def _check(resp: requests.Response, label: str) -> dict:
    """Log the request, raise on HTTP errors, and return the parsed JSON body."""
    method = resp.request.method if resp.request else "?"
    logger.debug("[%s] %s %s → %d", label, method, resp.url, resp.status_code)
    if resp.status_code >= 400:
        logger.error("[%s] %d response body:\n%s", label, resp.status_code, resp.text[:3000])
    resp.raise_for_status()
    return resp.json()  # type: ignore[no-any-return]


# ── API calls (pure REST, no SDK) ──────────────────────────────────────────

def create_data_room(session: requests.Session, cfg: BlockBrainConfig,
                     bot_id: str, convo_name: str, session_id: str) -> dict:
    """POST /cortex/active-bot/{bot_id}/convo  →  creates a new data-room."""
    payload = {
        "convoName": convo_name,
        "sessionId": session_id,
        "defaultLanguage": "English",
        "isDefaultConvoName": True,
        "enableGenerateImage": True,
        "model": cfg.chat_model,
        "imageModel": cfg.image_model, # TODO: this param is currently missing in the python API 
    }
    resp = session.post(_url(cfg, f"/cortex/active-bot/{bot_id}/convo"), json=payload)
    return _check(resp, "create_data_room")


def change_model(session: requests.Session, cfg: BlockBrainConfig,
                 convo_id: str, chat_model: str, image_model: str = "",
                 *, enable_generate_image: bool = False) -> dict:
    """PATCH /cortex/conversation/{convo_id}  →  update model / flags."""
    payload: dict = {"model": chat_model}
    if image_model:
        payload["imageModel"] = image_model
    if enable_generate_image:
        payload["enableGenerateImage"] = True
    logger.debug("[change_model] payload: %s", payload)
    resp = session.patch(_url(cfg, f"/cortex/conversation/{convo_id}"), json=payload)
    return _check(resp, "change_model")


def upload_attachment(session: requests.Session, cfg: BlockBrainConfig,
                      file_path: Path, convo_id: str, session_id: str) -> dict:
    """POST /cortex/conversation/{convo_id}/attachment  (multipart/form-data).

    Returns the parsed JSON response.  The attachment ``_id`` lives in
    ``result["body"]["_id"]`` and MUST be used in the ``files`` field of
    subsequent prompts (not the filename).
    """
    mime_type, _ = mimetypes.guess_type(str(file_path))
    if mime_type is None:
        mime_type = "application/octet-stream"

    url = _url(cfg, f"/cortex/conversation/{convo_id}/attachment")
    logger.info("Uploading %s (%s, %d bytes) to %s",
                file_path.name, mime_type, file_path.stat().st_size, url)

    with open(file_path, "rb") as fh:
        files = {"attachment": (file_path.name, fh, mime_type)}
        data = {"session_id": session_id}
        # Override the session-level Content-Type so requests generates
        # "multipart/form-data; boundary=…" automatically.
        resp = session.post(url, files=files, data=data,
                            headers={"Content-Type": None})

    result = _check(resp, "upload_attachment")

    body = result.get("body", {}) if isinstance(result, dict) else {}
    if isinstance(body, dict):
        logger.info("Upload status: %s  fileType: %s",
                     body.get("status"), body.get("fileType"))
    return result


def get_attachment_status(session: requests.Session, cfg: BlockBrainConfig,
                          convo_id: str) -> dict:
    """GET /cortex/conversation/{convo_id}/attachment  →  list attachments."""
    resp = session.get(_url(cfg, f"/cortex/conversation/{convo_id}/attachment"))
    return _check(resp, "attachment_status")


def wait_for_processing(session: requests.Session, cfg: BlockBrainConfig,
                        convo_id: str,
                        timeout: int = 300, poll_interval: int = 3) -> bool:
    """Poll attachment status until all files leave IN_PROGRESS."""
    start = time.time()
    logger.info("Polling attachment status (timeout=%ds, interval=%ds)",
                timeout, poll_interval)

    while time.time() - start < timeout:
        elapsed = time.time() - start
        try:
            result = get_attachment_status(session, cfg, convo_id)
            body = result.get("body")

            if not body:
                logger.debug("[%.1fs] No attachments listed yet — retrying", elapsed)
                time.sleep(poll_interval)
                continue

            files = body if isinstance(body, list) else [body]

            still_processing = False
            for f in files:
                name = f.get("name", "?")
                status = f.get("status", "UNKNOWN")
                calc = f.get("calculatedStatus", "")
                ftype = f.get("fileType", "")
                tokens = f.get("tokens", 0)
                logger.debug("[%.1fs]  %s: status=%s  calculated=%s  type=%s  tokens=%s",
                             elapsed, name, status, calc, ftype, tokens)
                if status == "IN_PROGRESS":
                    still_processing = True

            if not still_processing:
                logger.info("All attachments processed.")
                return True

        except Exception as exc:
            logger.warning("[%.1fs] Status-check error: %s", elapsed, exc)

        time.sleep(poll_interval)

    logger.error("TIMEOUT after %ds — file processing did not complete.", timeout)
    return False


def send_prompt(session: requests.Session, cfg: BlockBrainConfig,
                convo_id: str, session_id: str,
                content: str, model: str,
                files: list[str] | None = None,
                stream: bool = False) -> str:
    """POST /cortex/completions/v2/user-input  →  send a user message.

    Returns the raw response text (SSE stream or JSON depending on *stream*).
    """
    payload: dict = {
        "content": content,
        "actionType": "user",
        "messageType": "user-question",
        "sessionId": session_id,
        "convoId": convo_id,
    }
    if model:
        payload["model"] = model
    if files:
        payload["files"] = files

    headers: dict = {}
    if stream:
        headers["Accept"] = "text/event-stream"

    resp = session.post(
        _url(cfg, "/cortex/completions/v2/user-input"),
        json=payload,
        headers=headers,
        stream=stream,
    )

    # Use the same error-handling pattern as _check (log + raise_for_status)
    # but return raw text instead of parsed JSON, since the body is SSE data.
    logger.debug("[send_prompt] %s %s → %d  content-type=%s",
                 resp.request.method, resp.url, resp.status_code,
                 resp.headers.get("content-type", "?"))
    resp.raise_for_status()
    return resp.text


# ── SSE response parsing ───────────────────────────────────────────────────

def extract_signed_url(raw_stream_data: str) -> str | None:
    """Parse SSE stream text and return the first signedUrl from a
    ``generated_media`` event."""
    blocks = raw_stream_data.strip().split("\n\n")

    for block in blocks:
        if "event: generated_media" in block:
            for line in block.split("\n"):
                if line.startswith("data: "):
                    json_str = line[6:].strip()  # safer than str.replace
                    data_obj = json.loads(json_str)
                    media_urls = data_obj.get("mediaUrls", [])
                    if media_urls:
                        return media_urls[0].get("signedUrl")

    return None


# ── Orchestration helpers ───────────────────────────────────────────────────

def _setup_data_room(session: requests.Session, cfg: BlockBrainConfig,
                     session_id: str) -> str:
    """Create a data room, configure model + image generation.

    Returns the ``convo_id``.
    """
    convo_name = f"image_gen_{session_id[:8]}"

    logger.info("[1] Creating data room …")
    dr = create_data_room(session, cfg, cfg.bot_id, convo_name, session_id)
    convo_id: str = dr["body"]["dataRoomId"]
    logger.info("    convo_id = %s", convo_id)

    logger.info("[2] Setting model …")
    change_model(session, cfg, convo_id,
                 chat_model=cfg.chat_model,
                 image_model=cfg.image_model,
                 enable_generate_image=True)
    logger.info("    Chat model: %s  |  Image model: %s (image gen enabled)",
                cfg.chat_model, cfg.image_model)

    return convo_id


def _upload_and_wait(session: requests.Session, cfg: BlockBrainConfig,
                     image_path: Path, convo_id: str,
                     session_id: str) -> str:
    """Upload *image_path*, wait for processing, and return the attachment ``_id``."""
    logger.info("[3] Uploading attachment: %s", image_path.resolve())
    upload_result = upload_attachment(session, cfg, image_path, convo_id, session_id)

    if isinstance(upload_result, dict) and upload_result.get("error"):
        raise RuntimeError("Upload failed")

    attachment_id: str = upload_result.get("body", {}).get("_id", "")
    logger.info("    attachment_id = %s", attachment_id)

    logger.info("[4] Waiting for attachment processing …")
    if wait_for_processing(session, cfg, convo_id, timeout=300, poll_interval=3):
        logger.info("File processed successfully.")
        time.sleep(POST_PROCESSING_SETTLE_SECONDS)

        # If we didn't get the _id from upload, grab it from status
        if not attachment_id:
            status = get_attachment_status(session, cfg, convo_id)
            files_list = status.get("body", [])
            if isinstance(files_list, list) and files_list:
                attachment_id = files_list[0].get("_id", "")
                logger.info("    attachment_id (from status) = %s", attachment_id)
    else:
        logger.warning("File processing may not have completed.")

    if not attachment_id:
        raise RuntimeError("Could not determine attachment _id")

    return attachment_id


def _send_and_download(session: requests.Session, cfg: BlockBrainConfig,
                       convo_id: str, session_id: str,
                       prompt: str, attachment_id: str,
                       uploaded_filename: str, filename: str) -> None:
    """Send the image-generation prompt, download the result, save to *filename*."""
    full_prompt = f"Look at the file named {uploaded_filename}. {prompt}"

    logger.info("[5] Sending image-generation prompt …")
    logger.debug("    files (attachment IDs): [%s]", attachment_id)

    raw_response = send_prompt(
        session, cfg, convo_id, session_id,
        content=full_prompt,
        model=cfg.chat_model,
        files=[attachment_id],
        stream=False,
    )
    logger.info("Prompt response received.")

    image_url = extract_signed_url(raw_response)

    if not image_url:
        logger.error("No image URL found in response.\n%s", raw_response[:2000])
        return

    logger.info("Downloading image from: %s", image_url)
    img_resp = session.get(image_url, stream=True)
    img_resp.raise_for_status()

    with open(filename, "wb") as f:
        for chunk in img_resp.iter_content(chunk_size=8192):
            f.write(chunk)

    logger.info("Successfully saved image to %s", filename)


# ── Main flow ───────────────────────────────────────────────────────────────

def generate_blockbrain_image(
    prompt: str,
    image_path: Path,
    filename: str = "output_image.png",
    cfg: BlockBrainConfig | None = None,
) -> None:
    """Generate an image using the BlockBrain REST API (no SDK).

    Parameters
    ----------
    prompt:
        The text instruction describing the desired image modification.
    image_path:
        Path to the source image to upload as context.
    filename:
        Destination path for the generated image.
    cfg:
        Optional configuration; defaults are loaded from environment variables.
    """
    if cfg is None:
        cfg = BlockBrainConfig()

    if not cfg.api_token or not cfg.bot_id:
        logger.error("Missing api_token or bot_id in config.")
        return

    if not image_path.exists():
        logger.error("Image file not found: %s", image_path.absolute())
        return

    logger.info("--- BlockBrain image generation ---")
    logger.info("    Base URL: %s  |  Chat model: %s  |  Image model: %s",
                cfg.api_base, cfg.chat_model, cfg.image_model)

    session = _build_session(cfg)

    try:
        session_id = str(uuid.uuid4())
        convo_id = _setup_data_room(session, cfg, session_id)
        attachment_id = _upload_and_wait(session, cfg, image_path, convo_id, session_id)
        _send_and_download(session, cfg, convo_id, session_id,
                           prompt, attachment_id, image_path.name, filename)
    except Exception as e:
        logger.exception("An error occurred: %s", e)


# ── CLI entry point ─────────────────────────────────────────────────────────

def _parse_args() -> "argparse.Namespace":

    parser = argparse.ArgumentParser(
        description="Generate an image via the BlockBrain REST API.",
    )
    parser.add_argument(
        "-i", "--input",
        type=Path,
        default=Path("example.jpg"),
        help="Path to the source image to upload (default: example.jpg)",
    )
    parser.add_argument(
        "-o", "--output",
        type=str,
        default="output_image.png",
        help="Destination path for the generated image (default: output_image.png)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s: %(message)s")

    args = _parse_args()

    user_prompt = os.getenv("BLOCKBRAIN_USER_PROMPT", "")
    if not user_prompt:
        logger.error("BLOCKBRAIN_USER_PROMPT is not set in .env")
        raise SystemExit(1)

    generate_blockbrain_image(user_prompt, image_path=args.input, filename=args.output)
