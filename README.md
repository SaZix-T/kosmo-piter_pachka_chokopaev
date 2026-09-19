# Прогнозирование внешних рисков и планирование ВКД на МКС

Исследовательский прототип поддержки решений для наземной группы планирования
внекорабельной деятельности (ВКД). Сервис собирает данные из открытых источников,
строит линии риска на траектории МКС и сравнивает окна проведения работ.

**КосмоХакатон 2026 · Федеральный проект «Кадры для космоса»**

---

## Что делает

1. Пользователь задаёт режим (текущий / исторический), начало окна, длительность
   (1–8 ч) и период поиска (до 24 ч).
2. Сервис собирает данные из открытых источников (NOAA SWPC, GFZ, NASA DONKI,
   NCEI, Space-Track, CelesTrak).
3. Считает две линии риска: **радиационную обстановку** (SEP + геомагнитная буря)
   и **сближения с космическим мусором** (CDM / SOCRATES).
4. Сравнивает окна одинаковой длительности и выдаёт рекомендацию с обоснованием.
5. Показывает положение МКС на 3D-глобусе на момент рекомендованного окна.

**Не является системой допуска к реальным ВКД.** Прототип демонстрирует логику
обработки открытых данных и объяснимость выводов.

---

## Источники данных

| Источник | Что даёт | Режим |
|---|---|---|
| NOAA SWPC | протоны GOES, Kp, alerts, 3-day forecast | current |
| NOAA NCEI GOES SGPS | архив протонов (NetCDF) | current + historical |
| NOAA NCEI RSGA | ежедневные отчёты SWPC | current + historical |
| GFZ Potsdam | Kp (nowcast / definitive) | current + historical |
| NASA DONKI | SEP, GST, CMEAnalysis, notifications | current + historical |
| Space-Track | gp_history (TLE), cdm_public | historical |
| CelesTrak | GP (TLE), SOCRATES Plus | current |

**Авторизация:**
- Space-Track — логин и пароль (регистрация с подтверждением email).
- NASA API — ключ (можно DEMO_KEY с лимитом 50/сутки).
- Остальные — без ключа.

---

## Требования

- Python 3.11+
- Linux / macOS / WSL
- Интернет для обращения к внешним API

---

## Установка

```bash
git clone <repo-url>
cd kosmo-piter_pachka_chokopaev

python -m venv .venv
source .venv/bin/activate         # Linux/macOS
# .venv\Scripts\activate          # Windows

pip install -r requirements.txt


```env

# Space-Track (обязательно для historical TLE и CDM)
SPACETRACK_USER="your_login"
SPACETRACK_PASSWORD="your_password"

# NASA API (желательно — иначе DEMO_KEY с лимитом)
NASA_API_KEY="your_40_char_key"

# Спутник GOES для архива (16 — East, 18 — West, 19 — current)
GOES_SAT="16"

# Директория HTTP-кэша
CACHE_DIR="cache"