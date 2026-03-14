# models.py
import time
import os
import json
import hashlib
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

import requests

from config import settings
from llm_server import llm_server_up

logger = logging.getLogger(__name__)

# Kuinka kauan cache on voimassa (sekunteina)
_CACHE_TTL = 300.0  # 5 min

_cached_models_raw: Optional[List[Dict[str, Any]]] = None
_cached_at: float = 0.0

# Malli-metadatan sijainti (voit vaihtaa polkua env-muuttujalla MODEL_META_PATH)
_MODEL_META_FILE = os.getenv("MODEL_META_PATH", "model_meta.json")
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
            if name:
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
    return [
        m.get("name", "")
        for m in _get_raw_models()
        if m.get("name")
    ]


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

    for m in raw:
        name = m.get("name")
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
        else:
            present = mid in now_set

        row = {
            **e,
            "present_now": present,
        }
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
