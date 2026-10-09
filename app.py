import os
import uuid
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# --- API ключи из переменных окружения ---
GIGACHAT_AUTH_KEY = os.getenv("GIGACHAT_AUTH_KEY", "")   # Authorization key (Base64)
ACE_MUSIC_API_KEY = os.getenv("ACE_MUSIC_API_KEY", "")

GIGACHAT_OAUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
GIGACHAT_API_URL = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"
ACE_MUSIC_URL = "https://api.acemusic.ai/v1/chat/completions"


def get_gigachat_token():
    """Получает access token для GigaChat через Authorization key."""
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        "RqUID": str(uuid.uuid4()),
        "Authorization": f"Basic {GIGACHAT_AUTH_KEY}",  # ключ уже в Base64
    }
    data = {"scope": "GIGACHAT_API_PERS", "grant_type": "client_credentials"}
    try:
        resp = requests.post(
            GIGACHAT_OAUTH_URL,
            headers=headers,
            data=data,
            verify=False,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("access_token")
    except Exception as e:
        print(f"Ошибка получения токена GigaChat: {e}")
        return None


def generate_lyrics(theme, genre="pop"):
    """Генерирует текст песни через GigaChat."""
    if not GIGACHAT_AUTH_KEY:
        return (
            f"[Verse 1]\nЯ пою про {theme},\nСлова летят сквозь тишину...\n\n"
            f"[Chorus]\n{theme}, {theme},\nМы с тобой в одном огне."
        )

    token = get_gigachat_token()
    if not token:
        return "Ошибка авторизации в GigaChat. Проверь GIGACHAT_AUTH_KEY."

    prompt = (
        f"Ты профессиональный поэт-песенник. Напиши текст песни на русском языке.\n"
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
        resp = requests.post(
            GIGACHAT_API_URL, headers=headers, json=payload,
            verify=False, timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
    except requests.exceptions.HTTPError:
        if resp.status_code == 429:
            return "Дневной лимит GigaChat исчерпан. Попробуй позже."
        return "Ошибка GigaChat API"
    except Exception as e:
        return f"Ошибка генерации текста: {e}"


def generate_music(lyrics, prompt, duration=30):
    """Генерирует музыку с вокалом через ACE Music API."""
    if not ACE_MUSIC_API_KEY:
        return None, "ACE_MUSIC_API_KEY не задан в переменных окружения."

    headers = {
        "Authorization": f"Bearer {ACE_MUSIC_API_KEY}",
        "Content-Type": "application/json",
    }

    # Промпт уже обёрнут в <prompt>...</prompt> в вызывающем коде
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
            return None, "ACE Music API: таймаут (504). Попробуй увеличить длительность или повторить позже."
        if resp.status_code == 401:
            return None, "ACE Music API: неверный ключ (401). Проверь ACE_MUSIC_API_KEY."
        if resp.status_code == 429:
            return None, "ACE Music API: превышен лимит запросов (429)."

        resp.raise_for_status()
        data = resp.json()

        audio_b64 = None
        if "choices" in data:
            for choice in data["choices"]:
                msg = choice.get("message", {})
                if "audio" in msg:
                    audio_b64 = msg["audio"].get("data")
                    break
        elif "audio" in data:
            audio_b64 = data["audio"]

        if not audio_b64:
            return None, f"ACE Music API вернул ответ без аудио: {str(data)[:200]}"

        return f"data:audio/mp3;base64,{audio_b64}", None

    except requests.exceptions.Timeout:
        return None, "ACE Music API: превышено время ожидания (300 сек). Попробуй короче трек."
    except requests.exceptions.RequestException as e:
        return None, f"ACE Music API: ошибка сети — {e}"
    except Exception as e:
        return None, f"ACE Music API: неизвестная ошибка — {e}"


@app.route("/api/generate", methods=["POST"])
def generate():
    data = request.json or {}
    theme = (data.get("theme") or "").strip()
    genre = (data.get("genre") or "pop").strip()

    # Минимум 30 секунд — ACE Music часто падает на коротких запросах
    duration = int(data.get("duration", 30))
    if duration < 30:
        duration = 30
    if duration > 120:
        duration = 120

    if not theme:
        return jsonify({"error": "Тема не указана"}), 400

    # Шаг 1: текст
    lyrics = generate_lyrics(theme, genre)

    # Шаг 2: музыка — промпт обёрнут в <prompt>...</prompt>
    music_prompt = (
        f"<prompt>{genre} song, emotional, catchy melody, "
        f"theme about {theme}, russian vocals, warm atmosphere</prompt>"
    )
    music_data_uri, music_error = generate_music(lyrics, music_prompt, duration)

    return jsonify({
        "lyrics": lyrics,
        "music_url": music_data_uri,
        "has_music": music_data_uri is not None,
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