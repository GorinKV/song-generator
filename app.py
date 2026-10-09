import os
import uuid
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)  # Разрешаем запросы с фронтенда

# --- API ключи из переменных окружения ---
GIGACHAT_CLIENT_ID = os.getenv("GIGACHAT_CLIENT_ID", "")
GIGACHAT_CLIENT_SECRET = os.getenv("GIGACHAT_CLIENT_SECRET", "")
ACE_MUSIC_API_KEY = os.getenv("ACE_MUSIC_API_KEY", "")

GIGACHAT_OAUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
GIGACHAT_API_URL = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"
ACE_MUSIC_URL = "https://api.acemusic.ai/v1/chat/completions"


def get_gigachat_token():
    """Получает access token для GigaChat API."""
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        "RqUID": str(uuid.uuid4()),
    }
    data = {"scope": "GIGACHAT_API_PERS", "grant_type": "client_credentials"}
    try:
        resp = requests.post(
            GIGACHAT_OAUTH_URL, headers=headers, data=data,
            auth=(GIGACHAT_CLIENT_ID, GIGACHAT_CLIENT_SECRET),
            verify=False,  # GigaChat использует самоподписанный сертификат
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("access_token")
    except Exception as e:
        print(f"Ошибка получения токена GigaChat: {e}")
        return None


def generate_lyrics(theme, genre="pop"):
    """Генерирует текст песни через GigaChat."""
    if not GIGACHAT_CLIENT_ID or not GIGACHAT_CLIENT_SECRET:
        return (
            f"[Verse 1]\nЯ пою про {theme},\nСлова летят сквозь тишину...\n\n"
            f"[Chorus]\n{theme}, {theme},\nМы с тобой в одном огне."
        )

    token = get_gigachat_token()
    if not token:
        return "Ошибка авторизации в GigaChat. Проверь ключи."

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
        resp = requests.post(GIGACHAT_API_URL, headers=headers, json=payload,
                             verify=False, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
    except requests.exceptions.HTTPError:
        if resp.status_code == 429:
            return "Дневной лимит GigaChat исчерпан. Попробуй позже."
        return f"Ошибка GigaChat API"
    except Exception as e:
        return f"Ошибка генерации текста: {e}"


def generate_music(lyrics, prompt, duration=30):
    """Генерирует музыку с вокалом через ACE Music API."""
    if not ACE_MUSIC_API_KEY:
        return None

    headers = {
        "Authorization": f"Bearer {ACE_MUSIC_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "acemusic/acestep-v15-turbo",
        "messages": [{
            "role": "user",
            "content": f"<prompt>{prompt}</prompt><lyrics>{lyrics}</lyrics>",
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
            return None
        return f"data:audio/mp3;base64,{audio_b64}"
    except Exception:
        return None


@app.route("/api/generate", methods=["POST"])
def generate():
    data = request.json or {}
    theme = (data.get("theme") or "").strip()
    genre = (data.get("genre") or "pop").strip()
    duration = min(int(data.get("duration", 30)), 120)
    if not theme:
        return jsonify({"error": "Тема не указана"}), 400

    lyrics = generate_lyrics(theme, genre)
    music_prompt = f"{genre} song, emotional, catchy melody, theme about {theme}"
    music_data_uri = generate_music(lyrics, music_prompt, duration)

    return jsonify({
        "lyrics": lyrics,
        "music_url": music_data_uri,
        "has_music": music_data_uri is not None,
        "music_error": None if music_data_uri else "ACE Music API не вернул аудио.",
    })


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))