import os
import uuid
import json
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
        resp = requests.post(
            GIGACHAT_OAUTH_URL, headers=headers, data=data,
            verify=False, timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("access_token")
    except Exception as e:
        print(f"[GigaChat] Ошибка токена: {e}")
        return None


def generate_lyrics(theme, genre="pop"):
    if not GIGACHAT_AUTH_KEY:
        return f"[Verse 1]\nЯ пою про {theme},\nСлова летят сквозь тишину..."

    token = get_gigachat_token()
    if not token:
        return "Ошибка авторизации в GigaChat."

    prompt = (
        f"Ты профессиональный поэт-песенник. Напиши текст песни на русском.\n"
        f"Тема: {theme}\nЖанр: {genre}\n"
        f"Используй теги [Verse 1], [Chorus], [Verse 2], [Bridge], [Chorus]. "
        f"Без пояснений — только текст песни."
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
        resp = requests.post(
            GIGACHAT_API_URL, headers=headers, json=payload,
            verify=False, timeout=60,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"Ошибка генерации текста: {e}"


def detect_audio_mime(b64_str):
    """Определяет MIME-тип по первым байтам base64-строки."""
    try:
        sample = b64_str[:200]
        sample += "=" * ((4 - len(sample) % 4) % 4)
        raw = base64.b64decode(sample, validate=False)
    except Exception:
        return "audio/mpeg"

    if raw[:3] == b"ID3":
        return "audio/mpeg"
    if len(raw) >= 2 and raw[0] == 0xFF and (raw[1] & 0xE0) == 0xE0:
        return "audio/mpeg"
    if raw[:4] == b"RIFF" and len(raw) >= 12 and raw[8:12] == b"WAVE":
        return "audio/wav"
    if raw[:4] == b"OggS":
        return "audio/ogg"
    if raw[:4] == b"fLaC":
        return "audio/flac"
    if len(raw) >= 8 and raw[4:8] == b"ftyp":
        return "audio/mp4"
    return "audio/mpeg"


def find_all_long_strings(obj, min_len=5000, path=""):
    """Рекурсивно ищет все строки длиннее min_len."""
    results = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            results.extend(find_all_long_strings(v, min_len, f"{path}.{k}"))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            results.extend(find_all_long_strings(v, min_len, f"{path}[{i}]"))
    elif isinstance(obj, str) and len(obj) >= min_len:
        results.append((path, obj))
    return results


def debug_dump(obj, max_str=80, depth=0, max_depth=8):
    """Сжимает структуру для лога (обрезает длинные строки)."""
    if depth > max_depth:
        return "..."
    if isinstance(obj, dict):
        return {k: debug_dump(v, max_str, depth + 1, max_depth) for k, v in obj.items()}
    if isinstance(obj, list):
        return [debug_dump(x, max_str, depth + 1, max_depth) for x in obj[:3]]
    if isinstance(obj, str):
        if len(obj) > max_str:
            return f"<str len={len(obj)} head={obj[:max_str]!r}>"
        return obj
    return obj


def generate_music(lyrics, prompt, duration=30):
    if not ACE_MUSIC_API_KEY:
        return None, "ACE_MUSIC_API_KEY не задан."

    headers = {
        "Authorization": f"Bearer {ACE_MUSIC_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "acemusic/acestep-v15-turbo",
        "messages": [{
            "role": "user",
            "content": f"{prompt}<lyrics>{lyrics}</lyrics>",
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

        if resp.status_code == 504:
            return None, "ACE Music API: таймаут (504)."
        if resp.status_code == 401:
            return None, "ACE Music API: неверный ключ (401)."
        if resp.status_code == 429:
            return None, "ACE Music API: превышен лимит (429)."

        resp.raise_for_status()
        data = resp.json()

        # Полный дамп структуры
        dump = debug_dump(data, max_str=100)
        print(f"[ACE Music] Дамп: {json.dumps(dump, ensure_ascii=False)}")

        # Проверяем message.content (может быть текст ошибки)
        try:
            content = data["choices"][0]["message"].get("content", "")
            print(f"[ACE Music] message.content = {content!r}")
        except Exception:
            pass

        # Ищем длинные строки — от 5000 символов
        candidates = find_all_long_strings(data, min_len=5000)
        print(f"[ACE Music] Строк >5000: {len(candidates)}")

        if not candidates:
            # Пробуем порог ниже
            candidates = find_all_long_strings(data, min_len=1000)
            print(f"[ACE Music] Строк >1000: {len(candidates)}")

        for path, val in candidates:
            print(f"  {path}: len={len(val)}, head={val[:60]!r}")

        if not candidates:
            return None, f"Нет аудио-строк в ответе. Дамп: {json.dumps(dump, ensure_ascii=False)[:400]}"

        # Берём самую длинную
        path, audio_str = max(candidates, key=lambda x: len(x[1]))
        print(f"[ACE Music] Выбрано: {path}, длина={len(audio_str)}")

        # Если это URL — отдаём напрямую
        if audio_str.startswith(("http://", "https://")):
            return audio_str, None

        # Определяем MIME по магическим байтам
        mime = detect_audio_mime(audio_str)
        print(f"[ACE Music] MIME: {mime}")

        return f"data:{mime};base64,{audio_str}", None

    except requests.exceptions.Timeout:
        return None, "ACE Music API: превышено время ожидания."
    except Exception as e:
        return None, f"ACE Music API: ошибка — {e}"


@app.route("/api/generate", methods=["POST"])
def generate():
    data = request.json or {}
    theme = (data.get("theme") or "").strip()
    genre = (data.get("genre") or "pop").strip()

    duration = int(data.get("duration", 30))
    duration = max(30, min(duration, 120))

    if not theme:
        return jsonify({"error": "Тема не указана"}), 400

    lyrics = generate_lyrics(theme, genre)
    music_prompt = (
        f"<prompt>{genre} song, emotional, catchy melody, "
        f"theme about {theme}, russian vocals</prompt>"
    )
    music_url, music_error = generate_music(lyrics, music_prompt, duration)

    return jsonify({
        "lyrics": lyrics,
        "music_url": music_url,
        "has_music": music_url is not None,
        "music_error": music_error,
        "duration_used": duration,
    })


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "gigachat_configured": bool(GIGACHAT_AUTH_KEY),
        "ace_music_configured": bool(ACE_MUSIC_API_KEY),
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))