"""Ví dụ plugin: mọi file .py trong thư mục này được nạp tự động khi Cazung khởi động.
Copy file này để tự viết tool mới cho Cazung."""
import httpx

from cazung.tools import tool


@tool("Xem thời tiết hiện tại và dự báo ngắn của một thành phố.",
      {"city": "str: tên thành phố, vd 'Hanoi', 'Ho Chi Minh City'"}, group="web")
async def get_weather(city: str) -> str:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"https://wttr.in/{city}", params={"format": "j1", "lang": "vi"})
        r.raise_for_status()
        data = r.json()
    cur = data["current_condition"][0]
    desc = (cur.get("lang_vi") or cur.get("weatherDesc"))[0]["value"]
    lines = [f"{city}: {desc}, {cur['temp_C']}°C (cảm giác {cur['FeelsLikeC']}°C), "
             f"độ ẩm {cur['humidity']}%, gió {cur['windspeedKmph']} km/h"]
    for day in data.get("weather", [])[:3]:
        lines.append(f"- {day['date']}: {day['mintempC']}–{day['maxtempC']}°C")
    return "\n".join(lines)
