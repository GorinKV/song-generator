import os
import uuid
import base64
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

GIGACHAT_AUTH_KEY = os.getenv("GIGACHAT_AUTH_KEY", "")
ACE_MUSIC_API_KEY = os.getenv("ACE_MUSIC_API_KEY", "")

GIGACHAT_OAUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
GIGACHAT_API_URL = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"
ACE_MUSIC_URL = "https://api.acemusic.ai/v1/chat/completions"


def get_gigachat_token():
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        "RqUID": str(uuid.uuid4()),
        "Authorization": f"Basic {GIGACHAT_AUTH_KEY}",
    }
    data = {"scope": "GIGACHAT_API_PERS", "grant_type": "client_credentials"}
    try:
        resp = requests.post(GIGACHAT_OAUTH_URL, headers=headers, data=data,
                             verify=False, timeout=30)
        resp.raise_for_status()
        return resp.json().get("access_token")
    except Exception as e:
        print(f"[GigaChat] Ошибка токена: {e}")
        return None


def collect_all_strings(obj, path=""):
    results = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            results.extend(collect_all_strings(v, f"{path}.{k}"))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            results.extend(collect_all_strings(v, f"{path}[{i}]"))
    elif isinstance(obj, str):
        results.append((path, obj))
    return results


def decode_first_bytes(b64_str):
    s = b64_str
    if s.startswith("data:"):
        try:
            s = s.split(",", 1)[1]
        except Exception:
            return None, "no comma in data url"
    s = s.replace("\n", "").replace("\r", "")
    sample = s[:400]
    sample += "=" * ((4 - len(sample) % 4) % 4)
    try:
        raw = base64.b64decode(sample, validate=False)
        return raw[:16], raw[:16].hex()
    except Exception as e:
        return None, f"decode error: {e}"


def detect_mime_from_bytes(raw):
    if not raw:
        return "application/octet-stream"
    if raw[:3] == b"ID3":
        return "audio/mpeg"
    if raw[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xfa", b"\xff\xf2"):
        return "audio/mpeg"
    if raw[:4] == b"RIFF":
        return "audio/wav"
    if raw[:4] == b"OggS":
        return "audio/ogg"
    if raw[:4] == b"fLaC":
        return "audio/flac"
    if len(raw) >= 8 and raw[4:8] == b"ftyp":
        return "audio/mp4"
    if raw[:4] == b"\x1aE\xdf\xa3":
        return "audio/webm"
    return "application/octet-stream"


# ============ ЭТАП 1: ТЕКСТ ============

@app.route("/api/lyrics", methods=["POST"])
def api_lyrics():
    """Генерирует только текст песни. Быстро (5-15 сек)."""
    data = request.json or {}
    theme = (data.get("theme") or "").strip()
    genre = (data.get("genre") or "pop").strip()

    if not theme:
        return jsonify({"error": "Тема не указана"}), 400

    if not GIGACHAT_AUTH_KEY:
        return jsonify({
            "lyrics": f"[Verse 1]\nЯ пою про {theme},\nСлова летят сквозь тишину...",
            "debug": {"stage": "fallback", "error": "GIGACHAT_AUTH_KEY не задан"},
        })

    token = get_gigachat_token()
    if not token:
        return jsonify({"error": "Не удалось авторизоваться в GigaChat"}), 500

    prompt = (
        f"Ты профессиональный поэт-песенник. Напиши текст песни на русском.\n"
        f"Тема: {theme}\nЖанр: {genre}\n\n"
        f"Требования:\n"
        f"- Используй теги [Verse 1], [Chorus], [Verse 2], [Bridge], [Chorus]\n"
        f"- Пиши эмоционально, образно, рифмованно\n"
        f"- Без пояснений — только текст песни\n"
        f"- Минимум 3 куплета и припев"
    )
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {
        "model": "GigaChat",
        "messages": [
            {"role": "system", "content": "Ты поэт-песенник."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.9,
        "max_tokens": 2000,
    }

    try:
        resp = requests.post(GIGACHAT_API_URL, headers=headers, json=payload,
                             verify=False, timeout=60)
        resp.raise_for_status()
        lyrics = resp.json()["choices"][0]["message"]["content"].strip()
        return jsonify({
            "lyrics": lyrics,
            "debug": {"stage": "ok", "length": len(lyrics)},
        })
    except requests.exceptions.HTTPError:
        if resp.status_code == 429:
            return jsonify({"error": "Дневной лимит GigaChat исчерпан"}), 429
        return jsonify({"error": f"GigaChat HTTP {resp.status_code}"}), 500
    except Exception as e:
        return jsonify({"error": f"Ошибка генерации текста: {e}"}), 500


# ============ ЭТАП 2: МУЗЫКА ============

@app.route("/api/music", methods=["POST"])
def api_music():
    """Генерирует музыку по готовому тексту. Долго (1-3 мин)."""
    data = request.json or {}
    lyrics = (data.get("lyrics") or "").strip()
    theme = (data.get("theme") or "").strip()
    genre = (data.get("genre") or "pop").strip()
    duration = int(data.get("duration", 30))
    duration = max(30, min(duration, 120))

    if not lyrics:
        return jsonify({"error": "Текст песни не передан"}), 400

    debug = {
        "stage": "start",
        "strings_count": 0,
        "top_strings": [],
        "chosen_path": None,
        "chosen_len": None,
        "hex_head": None,
        "mime": None,
        "raw_preview": None,
        "error": None,
        "duration_used": duration,
    }

    if not ACE_MUSIC_API_KEY:
        debug["error"] = "ACE_MUSIC_API_KEY не задан"
        return jsonify({"music_url": None, "has_music": False,
                        "music_error": debug["error"], "debug": debug})

    headers = {
        "Authorization": f"Bearer {ACE_MUSIC_API_KEY}",
        "Content-Type": "application/json",
    }
    music_prompt = (
        f"<prompt>{genre} song, emotional, catchy melody, "
        f"theme about {theme}, russian vocals</prompt>"
    )
    payload = {
        "model": "acemusic/acestep-v15-turbo",
        "messages": [{
            "role": "user",
            "content": f"{music_prompt}<lyrics>{lyrics}</lyrics>",
        }],
        "stream": False,
        "audio_config": {
            "format": "mp3",
            "vocal_language": "ru",
            "duration": duration,
        },
    }

    try:
        resp = requests.post(ACE_MUSIC_URL, headers=headers, json=payload, timeout=300)
        debug["stage"] = f"http_{resp.status_code}"

        if resp.status_code == 504:
            debug["error"] = "таймаут (504)"
            return jsonify({"music_url": None, "has_music": False,
                            "music_error": "ACE Music: таймаут (504). Попробуй короче.",
                            "debug": debug})
        if resp.status_code == 401:
            debug["error"] = "неверный ключ (401)"
            return jsonify({"music_url": None, "has_music": False,
                            "music_error": "ACE Music: неверный ключ (401).",
                            "debug": debug})
        if resp.status_code == 429:
            debug["error"] = "лимит (429)"
            return jsonify({"music_url": None, "has_music": False,
                            "music_error": "ACE Music: превышен лимит (429).",
                            "debug": debug})

        resp.raise_for_status()
        resp_data = resp.json()
        debug["stage"] = "parsed_json"

        all_strings = collect_all_strings(resp_data)
        debug["strings_count"] = len(all_strings)
        all_strings.sort(key=lambda x: len(x[1]), reverse=True)

        for p, s in all_strings[:5]:
            debug["top_strings"].append({
                "path": p,
                "len": len(s),
                "head": s[:80],
                "is_data_url": s.startswith("data:"),
                "is_http_url": s.startswith(("http://", "https://")),
            })

        candidates = [(p, s) for p, s in all_strings if len(s) > 5000]
        if not candidates:
            debug["error"] = f"нет строк >5000 (всего {len(all_strings)})"
            return jsonify({"music_url": None, "has_music": False,
                            "music_error": debug["error"], "debug": debug})

        path, audio_str = candidates[0]
        debug["chosen_path"] = path
        debug["chosen_len"] = len(audio_str)
        debug["raw_preview"] = audio_str[:120]

        if audio_str.startswith("data:"):
            debug["mime"] = audio_str[5:25]
            return jsonify({"music_url": audio_str, "has_music": True,
                            "music_error": None, "debug": debug})

        if audio_str.startswith(("http://", "https://")):
            debug["mime"] = "http-url"
            return jsonify({"music_url": audio_str, "has_music": True,
                            "music_error": None, "debug": debug})

        raw_bytes, hex_repr = decode_first_bytes(audio_str)
        debug["hex_head"] = hex_repr

        if not raw_bytes:
            debug["error"] = f"decode failed: {hex_repr}"
            return jsonify({"music_url": None, "has_music": False,
                            "music_error": debug["error"], "debug": debug})

        mime = detect_mime_from_bytes(raw_bytes)
        debug["mime"] = mime

        if mime == "application/octet-stream":
            cleaned = audio_str.replace("\n", "").replace("\r", "")
            cleaned = cleaned.replace("-", "+").replace("_", "/")
            raw_bytes2, hex_repr2 = decode_first_bytes(cleaned)
            debug["hex_head_cleaned"] = hex_repr2
            if raw_bytes2:
                mime2 = detect_mime_from_bytes(raw_bytes2)
                debug["mime_cleaned"] = mime2
                if mime2 != "application/octet-stream":
                    return jsonify({"music_url": f"data:{mime2};base64,{cleaned}",
                                    "has_music": True, "music_error": None, "debug": debug})
            debug["error"] = f"неизвестный формат, hex={hex_repr}"
            return jsonify({"music_url": None, "has_music": False,
                            "music_error": debug["error"], "debug": debug})

        return jsonify({"music_url": f"data:{mime};base64,{audio_str}",
                        "has_music": True, "music_error": None, "debug": debug})

    except requests.exceptions.Timeout:
        debug["error"] = "timeout"
        return jsonify({"music_url": None, "has_music": False,
                        "music_error": "ACE Music: превышено время ожидания.",
                        "debug": debug})
    except Exception as e:
        import traceback
        debug["error"] = str(e)
        debug["traceback"] = traceback.format_exc()
        return jsonify({"music_url": None, "has_music": False,
                        "music_error": f"ACE Music: ошибка — {e}",
                        "debug": debug})


# ============ СТАРЫЙ ЭНДПОИНТ (для совместимости) ============

@app.route("/api/generate", methods=["POST"])
def generate_all():
    """Старый эндпоинт — генерирует всё сразу. Оставлен для совместимости."""
    data = request.json or {}
    theme = (data.get("theme") or "").strip()
    genre = (data.get("genre") or "pop").strip()
    duration = max(30, min(int(data.get("duration", 30)), 120))

    if not theme:
        return jsonify({"error": "Тема не указана"}), 400

    # Текст
    with app.test_request_context(json={"theme": theme, "genre": genre}):
        pass
    lyrics_resp = api_lyrics.__wrapped__() if hasattr(api_lyrics, "__wrapped__") else None
    # Проще — вызываем напрямую:
    try:
        token = get_gigachat_token() if GIGACHAT_AUTH_KEY else None
        if token:
            prompt = (f"Ты профессиональный поэт-песенник. Напиши текст песни на русском.\n"
                      f"Тема: {theme}\nЖанр: {genre}\n"
                      f"Используй теги [Verse 1], [Chorus], [Verse 2], [Bridge], [Chorus]. "
                      f"Без пояснений — только текст песни.")
            r = requests.post(GIGACHAT_API_URL,
                              headers={"Authorization": f"Bearer {token}",
                                       "Content-Type": "application/json"},
                              json={"model": "GigaChat",
                                    "messages": [{"role": "user", "content": prompt}],
                                    "temperature": 0.9, "max_tokens": 2000},
                              verify=False, timeout=60)
            r.raise_for_status()
            lyrics = r.json()["choices"][0]["message"]["content"].strip()
        else:
            lyrics = f"[Verse 1]\nЯ пою про {theme},\nСлова летят сквозь тишину..."
    except Exception as e:
        lyrics = f"Ошибка генерации текста: {e}"

    return jsonify({"lyrics": lyrics, "duration_used": duration})


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "gigachat_configured": bool(GIGACHAT_AUTH_KEY),
        "ace_music_configured": bool(ACE_MUSIC_API_KEY),
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))