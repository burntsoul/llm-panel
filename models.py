# models.py
import time
import os
import json
import hashlib
import logging
import re
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

import requests

from config import settings
from llm_server import llm_server_up
import llama_cpp_provider

logger = logging.getLogger(__name__)

# Kuinka kauan cache on voimassa (sekunteina)
_CACHE_TTL = 300.0  # 5 min

_cached_models_raw: Optional[List[Dict[str, Any]]] = None
_cached_at: float = 0.0

# Malli-metadatan sijainti (voit vaihtaa polkua env-muuttujalla MODEL_META_PATH)
_MODEL_META_FILE = os.getenv("MODEL_META_PATH", "model_meta.json")
_PROFILE_BACKING_PREFIX = "llm-agent/profile-"
_model_meta_cache: Optional[Dict[str, Dict[str, Any]]] = None

# Embedding cache: {hash(model + texts): (timestamp, embedding_vector)}
_embedding_cache: Dict[str, Tuple[float, List[List[float]]]] = {}


def _load_meta() -> Dict[str, Dict[str, Any]]:
    """
    Lukee model_meta.json -tiedoston (tai muuta polkua, jos MODEL_META_PATH asetettu)
    ja palauttaa sanakirjan:
      { "model_name": { ...meta... }, ... }

    Virhetilanteessa palauttaa tyhjän dictin.
    """
    global _model_meta_cache
    if _model_meta_cache is not None:
        return _model_meta_cache

    try:
        if os.path.exists(_MODEL_META_FILE):
            with open(_MODEL_META_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    _model_meta_cache = data
                    return data
    except Exception:
        pass

    _model_meta_cache = {}
    return _model_meta_cache


def _write_meta(meta: Dict[str, Dict[str, Any]]) -> None:
    meta_path = Path(_MODEL_META_FILE)
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = meta_path.with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
    tmp_path.replace(meta_path)
    _invalidate_model_meta_cache()


def profile_backing_model_name(public_model: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", public_model.strip()).strip("-").lower()
    if not slug:
        slug = "model"
    return f"{_PROFILE_BACKING_PREFIX}{slug}:latest"


def is_profile_backing_model(model_name: str) -> bool:
    return str(model_name or "").startswith(_PROFILE_BACKING_PREFIX)


def _profile_meta(meta: Dict[str, Any]) -> Dict[str, Any]:
    parameters = meta.get("profile_parameters")
    if not isinstance(parameters, dict):
        parameters = {}
    return {
        "enabled": bool(meta.get("profile_enabled")),
        "backing_model": str(meta.get("profile_backing_model") or ""),
        "base_model": str(meta.get("profile_base_model") or ""),
        "parameters": parameters,
        "system": meta.get("profile_system"),
        "status": str(meta.get("profile_status") or ""),
    }


def _live_model_name_set() -> Optional[set[str]]:
    now_models = _fetch_from_ollama()
    if now_models is None:
        return None
    return {m.get("name") for m in now_models if m.get("name")}


def get_profile_for_model(public_model: str) -> Optional[Dict[str, Any]]:
    meta = _load_meta().get(public_model, {})
    profile = _profile_meta(meta)
    if not profile["enabled"]:
        return None
    return profile


def resolve_model_for_upstream(public_model: str) -> str:
    profile = get_profile_for_model(public_model)
    if not profile:
        return public_model
    backing = profile.get("backing_model")
    if not backing:
        return public_model
    live_set = _live_model_name_set()
    if live_set is not None and backing not in live_set:
        return public_model
    return backing


def public_model_for_backing(backing_model: str) -> Optional[str]:
    for model_name, meta in _load_meta().items():
        profile = _profile_meta(meta)
        if profile["enabled"] and profile["backing_model"] == backing_model:
            return model_name
    return None


def rewrite_model_ids(value: Any, public_model: str, backing_model: str) -> Any:
    if not backing_model or backing_model == public_model:
        return value
    if isinstance(value, dict):
        return {k: rewrite_model_ids(v, public_model, backing_model) for k, v in value.items()}
    if isinstance(value, list):
        return [rewrite_model_ids(v, public_model, backing_model) for v in value]
    if isinstance(value, str):
        return value.replace(backing_model, public_model)
    return value


def upsert_model_profile(
    public_model: str,
    parameters: Dict[str, Any],
    system: Optional[str] = None,
) -> Dict[str, Any]:
    public_model = public_model.strip()
    if not public_model:
        raise ValueError("public_model is required")
    if is_profile_backing_model(public_model):
        raise ValueError("cannot create a profile for a private backing model")
    meta = _load_meta().copy()
    current = meta.get(public_model, {}).copy()
    backing = profile_backing_model_name(public_model)
    current.update(
        {
            "source": current.get("source", "local"),
            "device": current.get("device", "gpu"),
            "available": current.get("available", True),
            "profile_enabled": True,
            "profile_backing_model": backing,
            "profile_base_model": current.get("profile_base_model") or public_model,
            "profile_parameters": parameters,
            "profile_system": system,
            "profile_status": "active",
        }
    )
    meta[public_model] = current
    _write_meta(meta)
    return current


def delete_model_profile(public_model: str) -> Optional[Dict[str, Any]]:
    meta = _load_meta().copy()
    current = meta.get(public_model)
    if not current:
        return None
    current = current.copy()
    for key in [
        "profile_enabled",
        "profile_backing_model",
        "profile_base_model",
        "profile_parameters",
        "profile_system",
        "profile_status",
    ]:
        current.pop(key, None)
    meta[public_model] = current
    _write_meta(meta)
    return current


def get_model_profiles() -> List[Dict[str, Any]]:
    rows = []
    meta = _load_meta()
    live_set = _live_model_name_set()
    for model_name, values in meta.items():
        if is_profile_backing_model(model_name):
            continue
        profile = _profile_meta(values)
        if not profile["enabled"]:
            continue
        backing = profile["backing_model"]
        base = profile["base_model"] or model_name
        if live_set is None:
            status = "unknown"
            backing_present = None
            base_present = None
        else:
            backing_present = backing in live_set
            base_present = base in live_set
            if backing_present:
                status = "active"
            elif not base_present:
                status = "missing_base"
            else:
                status = "missing_backing"
        rows.append(
            {
                "public_model": model_name,
                "base_model": base,
                "backing_model": backing,
                "parameters": profile["parameters"],
                "system": profile["system"],
                "status": status,
                "base_present": base_present,
                "backing_present": backing_present,
            }
        )
    return rows


def _fetch_from_ollama() -> Optional[List[Dict[str, Any]]]:
    """
    Hakee tuoreen mallilistan Ollamalta /api/tags.
    Palauttaa listan dict-olioita tai None jos ei saada yhteyttä.
    """
    # Emme herätä llm-serveriä tätä varten, tarkistetaan vain onko se UP.
    if not llm_server_up():
        return None

    try:
        r = requests.get(
            f"http://{settings.LLM_HOST}:{settings.LLM_PORT}/api/tags",
            timeout=2,
        )
        r.raise_for_status()
        data = r.json()
        return data.get("models", [])
    except Exception:
        return None


def _invalidate_model_meta_cache() -> None:
    """Tyhjennä model_meta-välimuisti niin että seuraava _load_meta() lukee tiedostosta."""
    global _model_meta_cache
    _model_meta_cache = None


def invalidate_model_cache() -> None:
    """Clear cached Ollama model list and local metadata."""
    global _cached_models_raw, _cached_at
    _cached_models_raw = None
    _cached_at = 0.0
    _invalidate_model_meta_cache()


def sync_model_meta_with_ollama() -> bool:
    """
    Synkronoi model_meta.json Live Ollama-mallien kanssa.
    
    Strategia:
      1. Hae live-mallit Ollamalta
      2. Lataa nykyinen model_meta.json
      3. Yhdistä: säilytä olemassa oleva metatyöntö, lisää uudet mallit oletuksilla
      4. Merkitse poistetut mallit: "available": false
      5. Kirjoita atomisch (temp-tiedosto -> rename)
    
    Returns:
        True jos synkronointi onnistui, False jos virhe
    """
    try:
        # 1) Hae live-mallit Ollamalta
        live_models = _fetch_from_ollama()
        if live_models is None:
            logger.warning("Ollama eivät ole saatavilla - mallin meta-synkronointia ei voi tehdä.")
            return False
        
        live_model_names = set()
        for model in live_models:
            name = model.get("name")
            if name and not is_profile_backing_model(name):
                live_model_names.add(name)
        
        # 2) Lataa nykyinen model_meta.json
        existing_meta: Dict[str, Dict[str, Any]] = {}
        meta_path = Path(_MODEL_META_FILE)
        if meta_path.exists():
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    existing_meta = json.load(f)
                    if not isinstance(existing_meta, dict):
                        existing_meta = {}
            except Exception as e:
                logger.warning(f"Nykyisen model_meta.json lukeminen epäonnistui: {e}")
                existing_meta = {}
        
        # 3 & 4) Yhdistä ja päivitä
        merged: Dict[str, Dict[str, Any]] = {}
        
        # Säilytä ja päivitä olemassa olevat + lisää uudet
        for model_name in live_model_names:
            if model_name in existing_meta:
                # Säilytä olemassa oleva, mutta merkitse saatavilla
                merged[model_name] = existing_meta[model_name].copy()
                merged[model_name]["available"] = True
            else:
                # Uusi malli - lisää oletuksella
                merged[model_name] = {
                    "source": "local",
                    "device": "gpu",
                    "available": True,
                }
            logger.debug(f"Malli '{model_name}' synkronoitu (available: true)")
        
        # Merkitse poistetut mallit
        for model_name in existing_meta:
            if model_name not in live_model_names:
                merged[model_name] = existing_meta[model_name].copy()
                merged[model_name]["available"] = False
                logger.debug(f"Malli '{model_name}' merkitty poistetuksi (available: false)")
        
        # 5) Atomisch kirjoitus: temp-tiedosto -> rename
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = meta_path.with_suffix(".tmp")
        
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(merged, f, indent=2, ensure_ascii=False)
        
        # Atominen rename
        tmp_path.replace(meta_path)
        
        # Tyhjennä välimuisti niin että seuraava _load_meta() lukee päivitetyn tiedoston
        _invalidate_model_meta_cache()
        
        new_count = len(live_model_names)
        removed_count = sum(1 for m in merged.values() if not m.get("available", True))
        logger.info(
            f"model_meta.json synkronoitu: {new_count} mallia käytettävissä, "
            f"{removed_count} merkitty poistetuksi"
        )
        
        return True
        
    except Exception as e:
        logger.error(f"model_meta.json synkronointiin virhe: {e}", exc_info=True)
        return False


def _get_raw_models() -> List[Dict[str, Any]]:
    """
    Palauttaa 'raakatiedot' malleista:
      - ensin yritetään käyttää cachea (jos tuore)
      - sitten yritetään hakea Ollamalta
      - jos kumpikaan ei onnistu, käytetään DEFAULT_MODELS-listaa
    """
    global _cached_models_raw, _cached_at

    now = time.time()

    # 1) Cache vielä voimassa?
    if _cached_models_raw is not None and (now - _cached_at) < _CACHE_TTL:
        return _cached_models_raw

    # 2) Yritä hakea Ollamalta
    models = _fetch_from_ollama()
    if models is not None:
        _cached_models_raw = models
        _cached_at = now
        return models

    # 3) Jos Ollamalta ei onnistunut mutta cache on olemassa, käytä sitä
    if _cached_models_raw is not None:
        return _cached_models_raw

    # 4) Viimeinen fallback: DEFAULT_MODELS
    return [{"name": name} for name in settings.DEFAULT_MODELS]


def get_model_names() -> List[str]:
    """
    Palauttaa pelkät malli-id:t (esim. 'deepseek-coder:1.3b').
    (Käytettävissä jos halutaan vain string-lista.)
    """
    names = [
        m.get("name", "")
        for m in _get_raw_models()
        if m.get("name") and not is_profile_backing_model(m.get("name", ""))
    ]
    for name, meta in _load_meta().items():
        if _profile_meta(meta)["enabled"] and name not in names:
            names.append(name)
    for profile in llama_cpp_provider.list_profiles():
        served = str(profile.get("served_model_id") or "").strip()
        if served and served not in names:
            names.append(served)
    return names


def _badge_for_meta(source: str, device: str) -> str:
    """
    Rakentaa pienen 'badgen' meta-tietojen perusteella.
    """
    source = (source or "").lower()
    device = (device or "").lower()

    if source == "cloud":
        return "☁️ cloud"
    if device == "gpu":
        return "🟢 GPU-local"
    # oletus
    return "💻 CPU-local"


def get_model_display_entries() -> List[Dict[str, Any]]:
    """
    Palauttaa listan sanakirjoja llm-panelin UI:lle:

    [
      {
        "id": "deepseek-coder:6.7b",
        "label": "deepseek-coder:6.7b (💻 CPU-local)",
        "source": "local",
        "device": "cpu",
      },
      ...
    ]
    """
    raw = _get_raw_models()
    meta_map = _load_meta()

    entries: List[Dict[str, Any]] = []
    seen: set[str] = set()

    for m in raw:
        name = m.get("name")
        if not name:
            continue
        if is_profile_backing_model(name):
            continue
        seen.add(name)

        meta = meta_map.get(name, {})
        source = meta.get("source", "local")
        device = meta.get("device", "cpu")
        profile = _profile_meta(meta)

        badge = _badge_for_meta(source, device)
        if profile["enabled"]:
            badge = f"profile {badge}"
        label = f"{name} ({badge})"

        entries.append(
            {
                "id": name,
                "label": label,
                "source": source,
                "device": device,
                "profile": profile if profile["enabled"] else None,
            }
        )

    for name, meta in meta_map.items():
        if name in seen or is_profile_backing_model(name):
            continue
        profile = _profile_meta(meta)
        if not profile["enabled"]:
            continue
        source = meta.get("source", "local")
        device = meta.get("device", "cpu")
        badge = f"profile {_badge_for_meta(source, device)}"
        entries.append(
            {
                "id": name,
                "label": f"{name} ({badge})",
                "source": source,
                "device": device,
                "profile": profile,
            }
        )

    for profile in llama_cpp_provider.list_profiles():
        served = str(profile.get("served_model_id") or "").strip()
        if not served or served in seen:
            continue
        seen.add(served)
        entries.append(
            {
                "id": served,
                "label": f"{served} (llama.cpp GPU-local)",
                "source": "local",
                "device": "gpu",
                "provider": "llama_cpp",
                "profile": {
                    "enabled": True,
                    "provider": "llama_cpp",
                    "profile_id": profile.get("id"),
                    "gguf_path": profile.get("gguf_path"),
                    "status": profile.get("status", "stopped"),
                    "port": profile.get("port"),
                },
            }
        )

    # Jos jostain syystä lista tyhjä → fallback DEFAULT_MODELS
    if not entries:
        for name in settings.DEFAULT_MODELS:
            name = name.strip()
            if not name:
                continue
            meta = meta_map.get(name, {})
            source = meta.get("source", "local")
            device = meta.get("device", "cpu")
            badge = _badge_for_meta(source, device)
            label = f"{name} ({badge})"
            entries.append(
                {
                    "id": name,
                    "label": label,
                    "source": source,
                    "device": device,
                }
            )

    return entries


def get_models_openai_format() -> List[Dict[str, Any]]:
    """
    Palauttaa mallilistan OpenAI-yhteensopivassa muodossa
    /v1/models -endpointtia varten.

    Mukaan lisätään myös "metadata": { source, device } ja "description".
    """
    result: List[Dict[str, Any]] = []
    raw = _get_raw_models()
    meta_map = _load_meta()
    base_ts = 1730000000

    for idx, m in enumerate(raw):
        name = m.get("name")
        if not name:
            continue
        if is_profile_backing_model(name):
            continue

        meta = meta_map.get(name, {})
        source = meta.get("source", "local")
        device = meta.get("device", "cpu")
        profile = _profile_meta(meta)

        badge = _badge_for_meta(source, device)
        if profile["enabled"]:
            badge = f"profile {badge}"
        desc = f"{name} [{badge}]"

        result.append(
            {
                "id": name,
                "object": "model",
                "created": base_ts + idx,
                "owned_by": "llm-server",
                "metadata": {
                    "source": source,
                    "device": device,
                    "profile": profile if profile["enabled"] else None,
                },
                "description": desc,
            }
        )

    seen_names = {item["id"] for item in result}
    for name, meta in meta_map.items():
        if name in seen_names or is_profile_backing_model(name):
            continue
        profile = _profile_meta(meta)
        if not profile["enabled"]:
            continue
        source = meta.get("source", "local")
        device = meta.get("device", "cpu")
        badge = f"profile {_badge_for_meta(source, device)}"
        result.append(
            {
                "id": name,
                "object": "model",
                "created": base_ts + len(result),
                "owned_by": "llm-server",
                "metadata": {
                    "source": source,
                    "device": device,
                    "profile": profile,
                },
                "description": f"{name} [{badge}]",
            }
        )

    seen_names = {item["id"] for item in result}
    for profile in llama_cpp_provider.list_profiles():
        served = str(profile.get("served_model_id") or "").strip()
        if not served or served in seen_names:
            continue
        result.append(
            {
                "id": served,
                "object": "model",
                "created": base_ts + len(result),
                "owned_by": "llama.cpp",
                "metadata": {
                    "source": "local",
                    "device": "gpu",
                    "provider": "llama_cpp",
                    "profile": {
                        "enabled": True,
                        "provider": "llama_cpp",
                        "profile_id": profile.get("id"),
                        "gguf_path": profile.get("gguf_path"),
                        "status": profile.get("status", "stopped"),
                        "port": profile.get("port"),
                    },
                },
                "description": f"{served} [llama.cpp GPU-local]",
            }
        )

    # Jos jostain syystä tyhjä, fallback DEFAULT_MODELS
    if not result:
        for idx, name in enumerate(settings.DEFAULT_MODELS):
            name = name.strip()
            if not name:
                continue
            meta = meta_map.get(name, {})
            source = meta.get("source", "local")
            device = meta.get("device", "cpu")
            badge = _badge_for_meta(source, device)
            desc = f"{name} [{badge}]"
            result.append(
                {
                    "id": name,
                    "object": "model",
                    "created": base_ts + idx,
                    "owned_by": "llm-server",
                    "metadata": {
                        "source": source,
                        "device": device,
                    },
                    "description": desc,
                }
            )

    return result
def get_model_table_status() -> List[Dict[str, Any]]:
    """
    Palauttaa listan rivejä UI:lle ja /api/models -endpointille.

    [
      {
        "id": "deepseek-coder:6.7b",
        "label": "deepseek-coder:6.7b (💻 CPU-local)",
        "source": "local",
        "device": "cpu",
        "present_now": true / false / None
      },
      ...
    ]

    present_now:
      True  -> malli löytyy juuri nyt Ollaman /api/tags -listalta
      False -> ei löydy Ollamasta nyt (mutta on config/meta-listalla)
      None  -> Ollamaan ei saatu yhteyttä, tila tuntematon
    """
    entries = get_model_display_entries()

    # Haetaan _suoraan_ Ollamalta tämän hetken tilanne, ei cachea
    now_models = _fetch_from_ollama()
    if now_models is None:
        now_set = None
    else:
        now_set = {
            m.get("name")
            for m in now_models
            if m.get("name")
        }

    rows: List[Dict[str, Any]] = []
    for e in entries:
        mid = e["id"]
        if now_set is None:
            present = None
            base_present = None
        else:
            present = mid in now_set
            base_present = mid in now_set
            profile = e.get("profile") or {}
            backing = profile.get("backing_model")
            if profile and backing in now_set:
                present = True

        row = {
            **e,
            "present_now": present,
            "base_present_now": base_present,
        }
        if e.get("provider") == "llama_cpp":
            row["present_now"] = e.get("profile", {}).get("status") == "running"
            row["base_present_now"] = True
        rows.append(row)

    return rows


# ============================================================================
# Embedding Models Support
# ============================================================================

def _detect_embedding_models() -> List[str]:
    """
    Yrittää havaita embedding-mallit Ollamalta.
    Ollama ei erota chat- ja embedding-malleja /api/tags -vastauksessa,
    joten käytämme heuristiikkaa: jos mallin nimessä on 'embed' tai 'embedding',
    se on embedding-malli.
    
    Palauttaa listan embedding-mallien nimistä.
    """
    embedding_models = []
    raw = _get_raw_models()
    
    for model in raw:
        name = model.get("name", "").lower()
        # Heuristiikka: jos nimessä esiintyy 'embed' tai 'vec' tai 'dense'
        if any(keyword in name for keyword in ["embed", "embedding", "vec", "dense"]):
            embedding_models.append(model.get("name", ""))
    
    return embedding_models


def get_embedding_models_openai_format() -> List[Dict[str, Any]]:
    """
    Palauttaa embedding-mallilistan OpenAI-yhteensopivassa muodossa
    /api/embedding-models -endpointtia varten.
    """
    embedding_models = _detect_embedding_models()
    meta_map = _load_meta()
    base_ts = 1730000000
    
    result: List[Dict[str, Any]] = []
    
    for idx, model_name in enumerate(embedding_models):
        meta = meta_map.get(model_name, {})
        source = meta.get("source", "local")
        device = meta.get("device", "cpu")
        
        badge = _badge_for_meta(source, device)
        desc = f"{model_name} [{badge}]"
        
        # Try to get embedding dimension if available in metadata
        dimensions = meta.get("embedding_dimensions", 768)
        
        result.append(
            {
                "id": model_name,
                "object": "model",
                "created": base_ts + idx,
                "owned_by": "llm-server",
                "metadata": {
                    "source": source,
                    "device": device,
                    "embedding_dimensions": dimensions,
                    "type": "embedding",
                },
                "description": desc,
            }
        )
    
    return result


def _make_embedding_cache_key(model: str, texts: List[str]) -> str:
    """
    Luo cache-avaimen embedding-pyynölle.
    Käyttää SHA256-hashia mallin ja tekstien yhdistelmästä.
    """
    combined = f"{model}:{'|'.join(texts)}"
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()


def _clean_embedding_cache():
    """
    Siivoa vanhentuneista embedding-välimuistin merkinnöistä.
    """
    global _embedding_cache
    now = time.time()
    expired_keys = [
        key for key, (ts, _) in _embedding_cache.items()
        if (now - ts) > settings.EMBEDDING_CACHE_TTL
    ]
    for key in expired_keys:
        del _embedding_cache[key]


def get_cached_embeddings(model: str, texts: List[str]) -> Optional[List[Dict[str, Any]]]:
    """
    Hakee embeddings-välimuistista.
    Palauttaa embedding data array (array of embedding objects), 
    jos se on olemassa ja vielä voimassa.
    """
    _clean_embedding_cache()
    key = _make_embedding_cache_key(model, texts)
    
    if key in _embedding_cache:
        _, embeddings = _embedding_cache[key]
        return embeddings
    
    return None


def cache_embeddings(model: str, texts: List[str], embeddings: List[Dict[str, Any]]):
    """
    Tallentaa embeddings-välimuistiin.
    Expects the data array from Ollama's response (list of embedding objects).
    """
    key = _make_embedding_cache_key(model, texts)
    _embedding_cache[key] = (time.time(), embeddings)
