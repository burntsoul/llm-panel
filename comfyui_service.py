# comfyui_service.py
from __future__ import annotations

import asyncio
import base64
import copy
import datetime
import json
import logging
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
import requests

from config import settings
from llm_server import ensure_llm_running_with_reason, touch_activity
from state import get_maintenance_mode


logger = logging.getLogger(__name__)

_last_comfyui_activity = datetime.datetime.utcnow()
_workflow_cache: Optional[Dict[str, Any]] = None
_last_comfyui_error: Optional[str] = None


def touch_comfyui_activity() -> None:
    global _last_comfyui_activity
    _last_comfyui_activity = datetime.datetime.utcnow()


def set_comfyui_error(message: Optional[str]) -> None:
    global _last_comfyui_error
    _last_comfyui_error = message


def get_comfyui_last_error() -> Optional[str]:
    return _last_comfyui_error


def get_comfyui_last_activity() -> datetime.datetime:
    return _last_comfyui_activity


def _workflow_path() -> Path:
    return Path(settings.COMFYUI_WORKFLOW_PATH)


def _edit_workflow_path() -> Path:
    return Path(settings.COMFYUI_EDIT_WORKFLOW_PATH)


def _inpaint_workflow_path() -> Path:
    return Path(settings.COMFYUI_INPAINT_WORKFLOW_PATH)


def _load_workflow_template() -> Dict[str, Any]:
    global _workflow_cache
    if _workflow_cache is None:
        path = _workflow_path()
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError("ComfyUI workflow must be a JSON object")
        _workflow_cache = data
    return copy.deepcopy(_workflow_cache)


def _load_edit_workflow_template() -> Dict[str, Any]:
    path = _edit_workflow_path()
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("ComfyUI edit workflow must be a JSON object")
    return data


def _load_inpaint_workflow_template() -> Dict[str, Any]:
    path = _inpaint_workflow_path()
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("ComfyUI inpaint workflow must be a JSON object")
    return data


def _update_input(workflow: Dict[str, Any], node_id: str, key: str, value: Any) -> None:
    node = workflow.get(node_id)
    if not isinstance(node, dict):
        raise KeyError(f"Workflow node {node_id} not found")
    inputs = node.get("inputs")
    if not isinstance(inputs, dict):
        raise KeyError(f"Workflow node {node_id} inputs missing")
    inputs[key] = value


def _parse_size(size: str) -> Tuple[int, int]:
    if "x" not in size:
        raise ValueError("size must be formatted like 1024x1024")
    parts = size.lower().split("x", 1)
    width = int(parts[0])
    height = int(parts[1])
    if width <= 0 or height <= 0:
        raise ValueError("size must be positive")
    return width, height


def build_workflow(
    prompt: str,
    negative_prompt: str,
    width: int,
    height: int,
    batch_size: int,
    steps: int,
    cfg_scale: float,
    seed: int,
    sampler_name: str,
    scheduler: str,
    checkpoint_name: Optional[str],
) -> Dict[str, Any]:
    workflow = _load_workflow_template()

    _update_input(workflow, settings.COMFYUI_NODE_POSITIVE, "text", prompt)
    _update_input(workflow, settings.COMFYUI_NODE_NEGATIVE, "text", negative_prompt)
    _update_input(workflow, settings.COMFYUI_NODE_LATENT, "width", width)
    _update_input(workflow, settings.COMFYUI_NODE_LATENT, "height", height)
    _update_input(workflow, settings.COMFYUI_NODE_LATENT, "batch_size", batch_size)
    _update_input(workflow, settings.COMFYUI_NODE_SAMPLER, "steps", steps)
    _update_input(workflow, settings.COMFYUI_NODE_SAMPLER, "cfg", cfg_scale)
    _update_input(workflow, settings.COMFYUI_NODE_SAMPLER, "seed", seed)
    _update_input(workflow, settings.COMFYUI_NODE_SAMPLER, "sampler_name", sampler_name)
    _update_input(workflow, settings.COMFYUI_NODE_SAMPLER, "scheduler", scheduler)

    if checkpoint_name:
        _update_input(
            workflow,
            settings.COMFYUI_NODE_CHECKPOINT,
            "ckpt_name",
            checkpoint_name,
        )

    return workflow


def build_edit_workflow(
    prompt: str,
    negative_prompt: str,
    steps: int,
    cfg_scale: float,
    seed: int,
    sampler_name: str,
    scheduler: str,
    checkpoint_name: Optional[str],
    image_name: str,
    denoise: float,
) -> Dict[str, Any]:
    workflow = copy.deepcopy(_load_edit_workflow_template())

    _update_input(workflow, settings.COMFYUI_NODE_POSITIVE, "text", prompt)
    _update_input(workflow, settings.COMFYUI_NODE_NEGATIVE, "text", negative_prompt)
    _update_input(workflow, settings.COMFYUI_NODE_IMG2IMG_IMAGE, "image", image_name)
    _update_input(workflow, settings.COMFYUI_NODE_IMG2IMG_SAMPLER, "steps", steps)
    _update_input(workflow, settings.COMFYUI_NODE_IMG2IMG_SAMPLER, "cfg", cfg_scale)
    _update_input(workflow, settings.COMFYUI_NODE_IMG2IMG_SAMPLER, "seed", seed)
    _update_input(workflow, settings.COMFYUI_NODE_IMG2IMG_SAMPLER, "sampler_name", sampler_name)
    _update_input(workflow, settings.COMFYUI_NODE_IMG2IMG_SAMPLER, "scheduler", scheduler)
    _update_input(workflow, settings.COMFYUI_NODE_IMG2IMG_SAMPLER, "denoise", denoise)

    if checkpoint_name:
        _update_input(
            workflow,
            settings.COMFYUI_NODE_CHECKPOINT,
            "ckpt_name",
            checkpoint_name,
        )

    return workflow


def build_inpaint_workflow(
    prompt: str,
    negative_prompt: str,
    steps: int,
    cfg_scale: float,
    seed: int,
    sampler_name: str,
    scheduler: str,
    checkpoint_name: Optional[str],
    image_name: str,
    mask_name: str,
    denoise: float,
) -> Dict[str, Any]:
    workflow = copy.deepcopy(_load_inpaint_workflow_template())

    _update_input(workflow, settings.COMFYUI_NODE_POSITIVE, "text", prompt)
    _update_input(workflow, settings.COMFYUI_NODE_NEGATIVE, "text", negative_prompt)
    _update_input(workflow, settings.COMFYUI_NODE_INPAINT_IMAGE, "image", image_name)
    _update_input(workflow, settings.COMFYUI_NODE_INPAINT_MASK, "image", mask_name)
    _update_input(workflow, settings.COMFYUI_NODE_POSITIVE, "text", prompt)
    _update_input(workflow, settings.COMFYUI_NODE_NEGATIVE, "text", negative_prompt)
    _update_input(workflow, settings.COMFYUI_NODE_INPAINT_SAMPLER, "steps", steps)
    _update_input(workflow, settings.COMFYUI_NODE_INPAINT_SAMPLER, "cfg", cfg_scale)
    _update_input(workflow, settings.COMFYUI_NODE_INPAINT_SAMPLER, "seed", seed)
    _update_input(workflow, settings.COMFYUI_NODE_INPAINT_SAMPLER, "sampler_name", sampler_name)
    _update_input(workflow, settings.COMFYUI_NODE_INPAINT_SAMPLER, "scheduler", scheduler)
    _update_input(workflow, settings.COMFYUI_NODE_INPAINT_SAMPLER, "denoise", denoise)

    if checkpoint_name:
        _update_input(
            workflow,
            settings.COMFYUI_NODE_CHECKPOINT,
            "ckpt_name",
            checkpoint_name,
        )

    if settings.COMFYUI_INPAINT_VAE_NAME:
        _update_input(
            workflow,
            settings.COMFYUI_NODE_INPAINT_VAE_LOADER,
            "vae_name",
            settings.COMFYUI_INPAINT_VAE_NAME,
        )

    if settings.COMFYUI_INPAINT_REFINER_NAME:
        _update_input(
            workflow,
            settings.COMFYUI_NODE_INPAINT_REFINER_CHECKPOINT,
            "ckpt_name",
            settings.COMFYUI_INPAINT_REFINER_NAME,
        )
        _update_input(
            workflow,
            settings.COMFYUI_NODE_INPAINT_REFINER_SAMPLER,
            "denoise",
            settings.COMFYUI_INPAINT_REFINER_DENOISE,
        )
        _update_input(
            workflow,
            settings.COMFYUI_NODE_INPAINT_REFINER_SAMPLER,
            "steps",
            settings.COMFYUI_INPAINT_REFINER_STEPS,
        )
        _update_input(
            workflow,
            settings.COMFYUI_NODE_INPAINT_REFINER_POSITIVE,
            "text",
            prompt,
        )
        _update_input(
            workflow,
            settings.COMFYUI_NODE_INPAINT_REFINER_NEGATIVE,
            "text",
            negative_prompt,
        )

    return workflow


def _extract_prompt_images(history_data: Dict[str, Any], prompt_id: str) -> List[Dict[str, Any]]:
    data = history_data.get(prompt_id) or {}
    outputs = data.get("outputs", {}) if isinstance(data, dict) else {}
    images: List[Dict[str, Any]] = []
    for node in outputs.values():
        for image in node.get("images", []) if isinstance(node, dict) else []:
            if not isinstance(image, dict):
                continue
            images.append(image)
    return images


def _build_image_url(base_url: str, image: Dict[str, Any]) -> Optional[str]:
    filename = image.get("filename")
    if not filename:
        return None
    subfolder = image.get("subfolder", "")
    img_type = image.get("type", "output")
    return f"{base_url}/view?filename={filename}&subfolder={subfolder}&type={img_type}"


async def start_image_edit(
    prompt: str,
    negative_prompt: str,
    steps: int,
    cfg_scale: float,
    seed: int,
    sampler_name: str,
    scheduler: str,
    checkpoint_name: Optional[str],
    image_bytes: bytes,
    image_filename: str,
    denoise: float,
    mask_bytes: Optional[bytes] = None,
    mask_filename: Optional[str] = None,
) -> str:
    ready = await ensure_comfyui_ready()
    if not ready:
        raise RuntimeError(get_comfyui_last_error() or "ComfyUI not ready")

    base_url = settings.COMFYUI_BASE_URL.rstrip("/")

    async with httpx.AsyncClient(timeout=settings.COMFYUI_HTTP_TIMEOUT) as client:
        files = {"image": (image_filename, image_bytes, "application/octet-stream")}
        resp = await client.post(
            f"{base_url}/upload/image",
            files=files,
            data={"type": "input"},
        )
        resp.raise_for_status()
        upload = resp.json()
        image_name = upload.get("name")
        if not image_name:
            raise RuntimeError("ComfyUI image upload failed")

        mask_name = None
        if mask_bytes:
            mask_files = {"image": (mask_filename or "mask.png", mask_bytes, "application/octet-stream")}
            mask_resp = await client.post(
                f"{base_url}/upload/image",
                files=mask_files,
                data={"type": "input"},
            )
            mask_resp.raise_for_status()
            mask_upload = mask_resp.json()
            mask_name = mask_upload.get("name")
            if not mask_name:
                raise RuntimeError("ComfyUI mask upload failed")

        if mask_name:
            workflow = build_inpaint_workflow(
                prompt=prompt,
                negative_prompt=negative_prompt,
                steps=steps,
                cfg_scale=cfg_scale,
                seed=seed,
                sampler_name=sampler_name,
                scheduler=scheduler,
                checkpoint_name=checkpoint_name,
                image_name=image_name,
                mask_name=mask_name,
                denoise=denoise,
            )
        else:
            workflow = build_edit_workflow(
                prompt=prompt,
                negative_prompt=negative_prompt,
                steps=steps,
                cfg_scale=cfg_scale,
                seed=seed,
                sampler_name=sampler_name,
                scheduler=scheduler,
                checkpoint_name=checkpoint_name,
                image_name=image_name,
                denoise=denoise,
            )

        payload = {"prompt": workflow, "client_id": "llm-agent"}
        prompt_resp = await client.post(f"{base_url}/prompt", json=payload)
        if prompt_resp.status_code != 200:
            detail = prompt_resp.text.strip()
            set_comfyui_error(detail or "ComfyUI prompt failed")
            raise RuntimeError(f"ComfyUI prompt failed: {detail}")
        prompt_id = prompt_resp.json().get("prompt_id")
        if not prompt_id:
            raise RuntimeError("ComfyUI did not return prompt_id")

    touch_activity()
    touch_comfyui_activity()
    set_comfyui_error(None)
    return prompt_id


async def poll_prompt_preview(prompt_id: str) -> Dict[str, Any]:
    base_url = settings.COMFYUI_BASE_URL.rstrip("/")
    async with httpx.AsyncClient(timeout=settings.COMFYUI_HTTP_TIMEOUT) as client:
        history = await client.get(f"{base_url}/history/{prompt_id}")
        if history.status_code != 200:
            return {"done": False, "images": []}
        data = history.json()
        images = _extract_prompt_images(data, prompt_id)
        if not images:
            return {"done": False, "images": []}
        done = any(img.get("type") == "output" for img in images)
        urls = []
        for img in images:
            url = _build_image_url(base_url, img)
            if url:
                urls.append(url)
        return {"done": done, "images": urls}


def comfyui_up() -> bool:
    try:
        url = f"{settings.COMFYUI_BASE_URL}{settings.COMFYUI_READY_PATH}"
        response = requests.get(url, timeout=settings.COMFYUI_READY_TIMEOUT)
        return response.ok
    except Exception:
        return False


def _ssh_command(remote_cmd: str) -> Tuple[bool, str]:
    if not settings.COMFYUI_SSH_ENABLED:
        return False, "SSH control disabled"
    if not settings.COMFYUI_SSH_HOST or not settings.COMFYUI_SSH_USER:
        return False, "COMFYUI_SSH_HOST/COMFYUI_SSH_USER not configured"

    cmd = [
        "ssh",
        "-p",
        str(settings.COMFYUI_SSH_PORT),
    ]
    if settings.COMFYUI_SSH_KEY:
        cmd.extend(["-i", settings.COMFYUI_SSH_KEY])
    if not settings.COMFYUI_SSH_STRICT_HOST_KEY:
        cmd.extend(
            [
                "-o",
                "StrictHostKeyChecking=no",
                "-o",
                "UserKnownHostsFile=/dev/null",
            ]
        )
    cmd.append(f"{settings.COMFYUI_SSH_USER}@{settings.COMFYUI_SSH_HOST}")

    if settings.COMFYUI_SSH_USE_SUDO:
        remote_cmd = f"sudo -n {remote_cmd}"

    cmd.append(remote_cmd)

    logger.debug("ComfyUI SSH command: %s", remote_cmd)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=settings.COMFYUI_SSH_TIMEOUT,
            check=False,
        )
    except Exception as exc:
        logger.error("ComfyUI SSH command failed to run: %s", exc)
        return False, str(exc)

    if result.returncode != 0:
        stderr = result.stderr.strip() or "unknown error"
        logger.warning("ComfyUI SSH command failed: %s", stderr)
        return False, stderr
    return True, result.stdout.strip()


def start_comfyui_service() -> Tuple[bool, str]:
    if not settings.COMFYUI_SERVICE_NAME:
        return False, "COMFYUI_SERVICE_NAME not configured"
    systemctl = settings.COMFYUI_SYSTEMCTL_PATH or "systemctl"
    ok, msg = _ssh_command(f"{systemctl} start {settings.COMFYUI_SERVICE_NAME}")
    if not ok:
        logger.warning("ComfyUI service start failed: %s", msg)
    return ok, msg


def stop_comfyui_service() -> Tuple[bool, str]:
    if not settings.COMFYUI_SERVICE_NAME:
        return False, "COMFYUI_SERVICE_NAME not configured"
    systemctl = settings.COMFYUI_SYSTEMCTL_PATH or "systemctl"
    ok, msg = _ssh_command(f"{systemctl} stop {settings.COMFYUI_SERVICE_NAME}")
    if not ok:
        logger.warning("ComfyUI service stop failed: %s", msg)
    return ok, msg


async def ensure_comfyui_ready() -> bool:
    ok, _ = await asyncio.to_thread(ensure_llm_running_with_reason)
    if not ok:
        set_comfyui_error("LLM VM not ready")
        logger.warning("ComfyUI readiness failed: LLM VM not ready")
        return False

    if await asyncio.to_thread(comfyui_up):
        touch_activity()
        touch_comfyui_activity()
        set_comfyui_error(None)
        return True

    if settings.COMFYUI_SSH_ENABLED:
        started, msg = await asyncio.to_thread(start_comfyui_service)
        if not started:
            logger.warning("ComfyUI service start failed: %s", msg)
            set_comfyui_error(msg)
    else:
        logger.debug("ComfyUI SSH control disabled; not starting service")
        set_comfyui_error("SSH control disabled")

    deadline = time.monotonic() + settings.COMFYUI_READY_TIMEOUT
    while time.monotonic() < deadline:
        if await asyncio.to_thread(comfyui_up):
            touch_activity()
            touch_comfyui_activity()
            set_comfyui_error(None)
            return True
        await asyncio.sleep(settings.COMFYUI_POLL_INTERVAL)

    set_comfyui_error("ComfyUI readiness timed out")
    return False


async def comfyui_idle_shutdown_loop() -> None:
    while True:
        await asyncio.sleep(30)

        if get_maintenance_mode():
            continue

        if settings.COMFYUI_IDLE_SECONDS <= 0:
            continue

        if not settings.COMFYUI_SSH_ENABLED:
            continue

        if not await asyncio.to_thread(comfyui_up):
            continue

        idle = (datetime.datetime.utcnow() - _last_comfyui_activity).total_seconds()
        if idle >= settings.COMFYUI_IDLE_SECONDS:
            ok, msg = await asyncio.to_thread(stop_comfyui_service)
            if ok:
                logger.info("ComfyUI service stopped after idle timeout")
            else:
                logger.warning("Failed to stop ComfyUI service: %s", msg)


async def generate_images(
    prompt: str,
    negative_prompt: str,
    size: str,
    batch_size: int,
    steps: int,
    cfg_scale: float,
    seed: int,
    sampler_name: str,
    scheduler: str,
    checkpoint_name: Optional[str],
    response_format: str,
) -> List[Dict[str, str]]:
    ready = await ensure_comfyui_ready()
    if not ready:
        raise RuntimeError(get_comfyui_last_error() or "ComfyUI not ready")

    width, height = _parse_size(size)
    workflow = build_workflow(
        prompt=prompt,
        negative_prompt=negative_prompt,
        width=width,
        height=height,
        batch_size=batch_size,
        steps=steps,
        cfg_scale=cfg_scale,
        seed=seed,
        sampler_name=sampler_name,
        scheduler=scheduler,
        checkpoint_name=checkpoint_name,
    )

    base_url = settings.COMFYUI_BASE_URL.rstrip("/")
    payload = {"prompt": workflow, "client_id": "llm-agent"}

    async with httpx.AsyncClient(timeout=settings.COMFYUI_HTTP_TIMEOUT) as client:
        resp = await client.post(f"{base_url}/prompt", json=payload)
        resp.raise_for_status()
        prompt_id = resp.json().get("prompt_id")
        if not prompt_id:
            set_comfyui_error("ComfyUI did not return prompt_id")
            raise RuntimeError("ComfyUI did not return prompt_id")

        deadline = time.monotonic() + settings.COMFYUI_GENERATION_TIMEOUT
        images: List[Dict[str, str]] = []

        while time.monotonic() < deadline:
            history = await client.get(f"{base_url}/history/{prompt_id}")
            if history.status_code == 200:
                data = history.json().get(prompt_id) or {}
                outputs = data.get("outputs", {}) if isinstance(data, dict) else {}
                for node in outputs.values():
                    for image in node.get("images", []) if isinstance(node, dict) else []:
                        if not isinstance(image, dict):
                            continue
                        images.append(image)
                if images:
                    break
            await asyncio.sleep(settings.COMFYUI_POLL_INTERVAL)

        if not images:
            set_comfyui_error("ComfyUI generation timed out")
            raise RuntimeError("ComfyUI generation timed out")

        results: List[Dict[str, str]] = []
        for image in images[:batch_size]:
            filename = image.get("filename")
            subfolder = image.get("subfolder", "")
            img_type = image.get("type", "output")
            if not filename:
                continue

            if response_format == "url":
                url = (
                    f"{base_url}/view?filename={filename}"
                    f"&subfolder={subfolder}&type={img_type}"
                )
                results.append({"url": url})
                continue

            img_resp = await client.get(
                f"{base_url}/view",
                params={
                    "filename": filename,
                    "subfolder": subfolder,
                    "type": img_type,
                },
            )
            img_resp.raise_for_status()
            b64 = base64.b64encode(img_resp.content).decode("ascii")
            results.append({"b64_json": b64})

    touch_activity()
    touch_comfyui_activity()
    set_comfyui_error(None)
    return results


async def generate_image_edits(
    prompt: str,
    negative_prompt: str,
    steps: int,
    cfg_scale: float,
    seed: int,
    sampler_name: str,
    scheduler: str,
    checkpoint_name: Optional[str],
    response_format: str,
    image_bytes: bytes,
    image_filename: str,
    denoise: float,
    mask_bytes: Optional[bytes] = None,
    mask_filename: Optional[str] = None,
    n: int = 1,
) -> List[Dict[str, str]]:
    ready = await ensure_comfyui_ready()
    if not ready:
        raise RuntimeError(get_comfyui_last_error() or "ComfyUI not ready")

    base_url = settings.COMFYUI_BASE_URL.rstrip("/")

    async with httpx.AsyncClient(timeout=settings.COMFYUI_HTTP_TIMEOUT) as client:
        files = {"image": (image_filename, image_bytes, "application/octet-stream")}
        resp = await client.post(
            f"{base_url}/upload/image",
            files=files,
            data={"type": "input"},
        )
        resp.raise_for_status()
        upload = resp.json()
        image_name = upload.get("name")
        if not image_name:
            raise RuntimeError("ComfyUI image upload failed")

        mask_name = None
        if mask_bytes:
            mask_files = {"image": (mask_filename or "mask.png", mask_bytes, "application/octet-stream")}
            mask_resp = await client.post(
                f"{base_url}/upload/image",
                files=mask_files,
                data={"type": "input"},
            )
            mask_resp.raise_for_status()
            mask_upload = mask_resp.json()
            mask_name = mask_upload.get("name")
            if not mask_name:
                raise RuntimeError("ComfyUI mask upload failed")

        results: List[Dict[str, str]] = []
        total = max(1, int(n))
        for i in range(total):
            run_seed = seed + i
            if mask_name:
                workflow = build_inpaint_workflow(
                    prompt=prompt,
                    negative_prompt=negative_prompt,
                    steps=steps,
                    cfg_scale=cfg_scale,
                    seed=run_seed,
                    sampler_name=sampler_name,
                    scheduler=scheduler,
                    checkpoint_name=checkpoint_name,
                    image_name=image_name,
                    mask_name=mask_name,
                    denoise=denoise,
                )
            else:
                workflow = build_edit_workflow(
                    prompt=prompt,
                    negative_prompt=negative_prompt,
                    steps=steps,
                    cfg_scale=cfg_scale,
                    seed=run_seed,
                    sampler_name=sampler_name,
                    scheduler=scheduler,
                    checkpoint_name=checkpoint_name,
                    image_name=image_name,
                    denoise=denoise,
                )

            payload = {"prompt": workflow, "client_id": "llm-agent"}
            prompt_resp = await client.post(f"{base_url}/prompt", json=payload)
            if prompt_resp.status_code != 200:
                detail = prompt_resp.text.strip()
                set_comfyui_error(detail or "ComfyUI prompt failed")
                raise RuntimeError(f"ComfyUI prompt failed: {detail}")
            prompt_id = prompt_resp.json().get("prompt_id")
            if not prompt_id:
                raise RuntimeError("ComfyUI did not return prompt_id")

            deadline = time.monotonic() + settings.COMFYUI_GENERATION_TIMEOUT
            images: List[Dict[str, str]] = []

            while time.monotonic() < deadline:
                history = await client.get(f"{base_url}/history/{prompt_id}")
                if history.status_code == 200:
                    data = history.json().get(prompt_id) or {}
                    outputs = data.get("outputs", {}) if isinstance(data, dict) else {}
                    for node in outputs.values():
                        for image in node.get("images", []) if isinstance(node, dict) else []:
                            if not isinstance(image, dict):
                                continue
                            images.append(image)
                    if images:
                        break
                await asyncio.sleep(settings.COMFYUI_POLL_INTERVAL)

            if not images:
                raise RuntimeError("ComfyUI generation timed out")

            image = images[0]
            filename = image.get("filename")
            subfolder = image.get("subfolder", "")
            img_type = image.get("type", "output")
            if not filename:
                continue

            if response_format == "url":
                url = (
                    f"{base_url}/view?filename={filename}"
                    f"&subfolder={subfolder}&type={img_type}"
                )
                results.append({"url": url})
                continue

            img_resp = await client.get(
                f"{base_url}/view",
                params={
                    "filename": filename,
                    "subfolder": subfolder,
                    "type": img_type,
                },
            )
            img_resp.raise_for_status()
            b64 = base64.b64encode(img_resp.content).decode("ascii")
            results.append({"b64_json": b64})

    touch_activity()
    touch_comfyui_activity()
    set_comfyui_error(None)
    return results
