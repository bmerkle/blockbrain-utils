from __future__ import annotations

"""BlockBrain image generation — uses the blockbrain_api SDK."""

import argparse
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import requests
from blockbrain_api import BlockBrainAPI
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ── Constants ───────────────────────────────────────────────────────────────

POST_PROCESSING_SETTLE_SECONDS = 5
"""Seconds to wait after file processing completes, allowing indexing to commit."""


# ── Configuration ───────────────────────────────────────────────────────────

@dataclass
class BlockBrainConfig:
    """All settings required to talk to the BlockBrain API via the SDK.

    Defaults are drawn from environment variables at import time.
    Pass explicit values to override.
    """

    api_base: str = os.getenv("BLOCKBRAIN_API_BASE", "https://blocky.theblockbrain.ai")
    api_token: str = os.getenv("BLOCKBRAIN_API_TOKEN", "")
    bot_id: str = os.getenv("BLOCKBRAIN_BOT_ID", "")
    chat_model: str = os.getenv("BLOCKBRAIN_CHAT_MODEL", "google-gemini-2.5-flash")
    image_model: str = os.getenv("BLOCKBRAIN_IMAGE_MODEL", "google-vertex-25-flash-image")
    tenant_domain: str = os.getenv("BLOCKBRAIN_TENANT_DOMAIN", "sick")


# ── Orchestration helpers ───────────────────────────────────────────────────

def _init_api(cfg: BlockBrainConfig) -> BlockBrainAPI:
    """Create and return a configured BlockBrainAPI client."""
    return BlockBrainAPI(
        base_url=cfg.api_base,
        token=cfg.api_token,
        bot_id=cfg.bot_id,
        tenant_domain=cfg.tenant_domain,
        default_model=cfg.chat_model,
        enable_logging=True,
    )


def _setup_data_room(api: BlockBrainAPI, cfg: BlockBrainConfig,
                     session_id: str) -> str:
    """Create a data room, configure model + image generation.

    Returns the ``convo_id``.
    """
    convo_name = f"image_gen_{session_id[:8]}"

    logger.info("[1] Creating data room …")
    dr = api.core.create_data_room(
        convo_name, session_id, cfg.bot_id, model=cfg.chat_model
    )
    convo_id: str = dr["body"]["dataRoomId"]
    logger.info("    convo_id = %s", convo_id)

    logger.info("[2] Setting model …")
    api.core.change_model(
        convo_id,
        model=cfg.chat_model,
        enable_generate_image=True,
    )
    logger.info("    Chat model: %s  (image gen enabled)", cfg.chat_model)

    return convo_id


def _upload_and_wait(api: BlockBrainAPI, cfg: BlockBrainConfig,
                     image_path: Path, convo_id: str,
                     session_id: str) -> str:
    """Upload *image_path*, wait for processing, return the attachment ``_id``."""
    logger.info("[3] Uploading attachment: %s", image_path.resolve())
    upload_result = api.core.upload_file(str(image_path), convo_id, session_id)

    if isinstance(upload_result, dict) and upload_result.get("error"):
        raise RuntimeError("Upload failed")

    attachment_id: str = upload_result.get("body", {}).get("_id", "")
    logger.info("    attachment_id = %s", attachment_id)

    logger.info("[4] Waiting for attachment processing …")
    processing_result = api.core.wait_for_file_processing(
        convo_id, timeout=300, poll_interval=3
    )

    processing_ok = (
        isinstance(processing_result, dict)
        and processing_result.get("success")
    )

    if processing_ok:
        logger.info("File processed successfully.")
        time.sleep(POST_PROCESSING_SETTLE_SECONDS)

        if not attachment_id:
            status = api.core.check_file_upload_status(convo_id)
            files_list = (
                status.get("body", []) if isinstance(status, dict) else []
            )
            if isinstance(files_list, list) and files_list:
                attachment_id = files_list[0].get("_id", "")
                logger.info("    attachment_id (from status) = %s", attachment_id)
    else:
        error_detail = (
            processing_result.get("error", "unknown")
            if isinstance(processing_result, dict)
            else str(processing_result)
        )
        logger.warning("File processing may not have completed: %s", error_detail)

    if not attachment_id:
        raise RuntimeError("Could not determine attachment _id")

    return attachment_id


def _send_and_download(api: BlockBrainAPI, cfg: BlockBrainConfig,
                       convo_id: str, session_id: str,
                       prompt: str, attachment_id: str,
                       uploaded_filename: str, filename: str) -> None:
    """Send the image-generation prompt, download the result, save to *filename*."""
    full_prompt = f"Look at the file named {uploaded_filename}. {prompt}"

    logger.info("[5] Sending image-generation prompt …")
    logger.debug("    files (attachment IDs): [%s]", attachment_id)

    raw_response = api.core.user_prompt(
        content=full_prompt,
        session_id=session_id,
        convo_id=convo_id,
        model=cfg.chat_model,
        files=[attachment_id],
        stream=False,
    )
    logger.info("Prompt response received.")

    # The SDK may return parsed JSON (dict/str) rather than raw SSE text.
    if isinstance(raw_response, str):
        image_url = extract_signed_url(raw_response)
    elif isinstance(raw_response, dict):
        media_urls = raw_response.get("mediaUrls", [])
        image_url = media_urls[0].get("signedUrl") if media_urls else None
    else:
        image_url = None

    if not image_url:
        response_preview = (
            raw_response[:2000] if isinstance(raw_response, str)
            else json.dumps(raw_response, indent=2)[:2000]
        )
        logger.error("No image URL found in response.\n%s", response_preview)
        return

    logger.info("Downloading image from: %s", image_url)
    headers = {"Authorization": f"Bearer {cfg.api_token}"}
    img_resp = requests.get(image_url, headers=headers, stream=True)
    img_resp.raise_for_status()

    with open(filename, "wb") as f:
        for chunk in img_resp.iter_content(chunk_size=8192):
            f.write(chunk)

    logger.info("Successfully saved image to %s", filename)


# ── SSE response parsing ───────────────────────────────────────────────────

def extract_signed_url(raw_stream_data: str) -> str | None:
    """Parse SSE stream text and return the first signedUrl from a
    ``generated_media`` event."""
    blocks = raw_stream_data.strip().split("\n\n")

    for block in blocks:
        if "event: generated_media" in block:
            for line in block.split("\n"):
                if line.startswith("data: "):
                    json_str = line[6:].strip()
                    data_obj = json.loads(json_str)
                    media_urls = data_obj.get("mediaUrls", [])
                    if media_urls:
                        return media_urls[0].get("signedUrl")

    return None


# ── Main flow ───────────────────────────────────────────────────────────────

def generate_blockbrain_image(
    prompt: str,
    image_path: Path,
    filename: str = "output_image.png",
    cfg: BlockBrainConfig | None = None,
) -> None:
    """Generate an image using the BlockBrain API SDK.

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

    logger.info("--- BlockBrain image generation (SDK) ---")
    logger.info("    Base URL: %s  |  Chat model: %s  |  Image model: %s",
                cfg.api_base, cfg.chat_model, cfg.image_model)

    api = _init_api(cfg)

    try:
        session_id = str(uuid.uuid4())
        convo_id = _setup_data_room(api, cfg, session_id)
        attachment_id = _upload_and_wait(api, cfg, image_path, convo_id, session_id)
        _send_and_download(api, cfg, convo_id, session_id,
                           prompt, attachment_id, image_path.name, filename)
    except Exception as e:
        logger.exception("An error occurred: %s", e)


# ── CLI entry point ─────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate an image via the BlockBrain SDK.",
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
