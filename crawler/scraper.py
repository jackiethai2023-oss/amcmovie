#!/usr/bin/env python3
"""
AMC电影院周末排片爬虫
通过 requests 抓取 HTML，用正则从 React Server Components (RSC) Payload 中提取排片数据
"""

import requests
import json
import logging
import os
import re
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 目标影厅配置（只修改日期部分）
THEATERS = [
    {
        'name': 'Century City IMAX',
        'url': 'https://www.amctheatres.com/movie-theatres/los-angeles/amc-century-city-15/showtimes?premium-offering=imax&date='
    },
    {
        'name': 'Century City Dolby Cinema',
        'url': 'https://www.amctheatres.com/movie-theatres/los-angeles/amc-century-city-15/showtimes?premium-offering=dolbycinemaatamcprime&date='
    },
    {
        'name': 'Universal CityWalk IMAX',
        'url': 'https://www.amctheatres.com/movie-theatres/los-angeles/universal-cinema-amc-at-citywalk-hollywood/showtimes?premium-offering=imax&date='
    }
]

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
}


def get_weekend_dates():
    """获取未来2周的周末日期（周六和周日）"""
    today = datetime.now()
    weekend_dates = []
    for i in range(14):
        check_date = today + timedelta(days=i)
        if check_date.weekday() in [5, 6]:  # 周六=5, 周日=6
            weekend_dates.append(check_date)
    return weekend_dates


def slug_to_title(slug):
    """把 movie slug 转为可读标题，如 'project-hail-mary' -> 'Project Hail Mary'"""
    return slug.replace('-', ' ').title()


def fetch_showtimes(theater, date_str):
    """
    抓取指定影厅和日期的排片信息
    从 RSC Payload 中用正则提取电影和场次数据
    """
    url = f"{theater['url']}{date_str}"
    logger.info(f"抓取 {theater['name']} {date_str} -> {url}")

    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        html = resp.text
        logger.info(f"获取到 HTML，长度: {len(html)}")

        # 诊断：搜索HTML中和电影/时间相关的关键模式
        debug_patterns = {
            'display': r'"display"',
            'time': r'"time"\s*:\s*"',
            'amPm': r'"amPm"',
            'showtime': r'showtime',
            'Showtime': r'Showtime',
            'aria-describedby': r'"aria-describedby"',
            'movieId': r'"movieId"',
            'movieName': r'"movieName"',
            'movie': r'"movie"',
            'title': r'"title"\s*:\s*"',
            'name': r'"name"\s*:\s*"',
            'slug': r'"slug"\s*:\s*"',
            'performanceNumber': r'"performanceNumber"',
            'AM/PM time': r'\d{1,2}:\d{2}\s*(am|pm|AM|PM)',
            'ISO time': r'T\d{2}:\d{2}:\d{2}',
        }
        for label, pat in debug_patterns.items():
            matches = re.findall(pat, html)
            if matches:
                logger.info(f"  [DEBUG] '{label}' 出现 {len(matches)} 次")
                # 显示第一个匹配的上下文
                m = re.search(pat, html)
                if m:
                    start = max(0, m.start() - 80)
                    end = min(len(html), m.end() + 80)
                    context = html[start:end].replace('\n', ' ')
                    logger.info(f"  [DEBUG] 上下文: ...{context}...")

        # 额外：保存前2000字符的HTML到日志，帮助分析结构
        logger.info(f"  [DEBUG] HTML前500字符: {html[:500]}")
        logger.info(f"  [DEBUG] 搜索 script 标签...")
        script_tags = re.findall(r'<script[^>]*>(.*?)</script>', html[:50000], re.DOTALL)
        for i, script in enumerate(script_tags[:5]):
            logger.info(f"  [DEBUG] script[{i}] 长度={len(script)}, 前200字符: {script[:200]}")

        # 从 RSC Payload 中提取电影和场次
        movies = parse_rsc_payload(html)

        logger.info(f"成功获取 {theater['name']} {date_str}: {len(movies)} 部电影")
        for m in movies:
            logger.info(f"  - {m['title']}: {m['showtimes']}")
        return movies

    except requests.RequestException as e:
        logger.error(f"请求失败 ({theater['name']} {date_str}): {e}")
        return []
    except Exception as e:
        logger.error(f"解析失败 ({theater['name']} {date_str}): {e}")
        return []


def parse_rsc_payload(html):
    """
    从 HTML 中的 RSC Payload 解析电影和场次信息

    RSC Payload 中包含类似这样的模式:
    - 场次时间: "display":{"time":"10:30","amPm":"am"}
    - 电影 slug: "aria-describedby":"movie-slug-here"
    """
    movies_dict = {}  # slug -> list of showtimes

    # 提取所有场次时间
    # 模式: "display":{"time":"HH:MM","amPm":"am/pm"}
    time_pattern = re.compile(
        r'"display"\s*:\s*\{\s*"time"\s*:\s*"(\d{1,2}:\d{2})"\s*,\s*"amPm"\s*:\s*"(am|pm)"\s*\}'
    )

    # 提取电影 slug
    # 模式: "aria-describedby":"some-movie-slug"
    slug_pattern = re.compile(
        r'"aria-describedby"\s*:\s*"([a-z0-9][a-z0-9\-]+[a-z0-9])"'
    )

    # 策略：找到每个电影 slug，然后在它附近找场次时间
    # RSC payload 中，一个电影的数据块通常包含 slug 和多个 display time

    # 先尝试按块解析：查找电影slug和时间的关联
    # 在 RSC payload 中，数据通常按电影分组

    # 找到所有 slug 出现的位置
    slugs_found = []
    for match in slug_pattern.finditer(html):
        slug = match.group(1)
        pos = match.start()
        # 过滤掉明显不是电影的 slug
        if len(slug) < 3 or slug in ('null', 'undefined', 'true', 'false'):
            continue
        # 过滤掉常见非电影 slug
        skip_keywords = ['sign-in', 'join', 'reward', 'promo', 'banner',
                         'header', 'footer', 'nav', 'menu', 'modal',
                         'cookie', 'consent', 'stubs', 'a-list']
        if any(kw in slug for kw in skip_keywords):
            continue
        slugs_found.append((slug, pos))

    # 找到所有时间出现的位置
    times_found = []
    for match in time_pattern.finditer(html):
        time_val = match.group(1)
        ampm = match.group(2)
        pos = match.start()
        times_found.append((f"{time_val} {ampm.upper()}", pos))

    logger.info(f"找到 {len(slugs_found)} 个电影slug, {len(times_found)} 个场次时间")

    if not times_found:
        logger.warning("未找到任何场次时间")
        return []

    # 将每个时间关联到最近的前一个 slug
    # 按位置排序所有找到的元素
    all_items = []
    for slug, pos in slugs_found:
        all_items.append(('slug', slug, pos))
    for time_str, pos in times_found:
        all_items.append(('time', time_str, pos))
    all_items.sort(key=lambda x: x[2])

    current_slug = None
    for item_type, value, pos in all_items:
        if item_type == 'slug':
            current_slug = value
            if current_slug not in movies_dict:
                movies_dict[current_slug] = []
        elif item_type == 'time' and current_slug:
            if value not in movies_dict[current_slug]:
                movies_dict[current_slug].append(value)

    # 如果上面的方法没找到关联，尝试全局提取
    if not movies_dict and times_found:
        logger.warning("无法关联电影和场次，尝试全局提取...")
        # 收集所有唯一的 slug
        unique_slugs = list(dict.fromkeys(s for s, _ in slugs_found))
        all_times = [t for t, _ in times_found]

        if len(unique_slugs) == 1:
            movies_dict[unique_slugs[0]] = all_times
        elif unique_slugs:
            # 平均分配时间给电影（最后手段）
            movies_dict[unique_slugs[0]] = all_times

    # 转换为输出格式
    movies = []
    for slug, showtimes in movies_dict.items():
        if showtimes:  # 只包含有场次的电影
            movies.append({
                'title': slug_to_title(slug),
                'showtimes': showtimes
            })

    return movies


def main():
    """主函数"""
    logger.info("开始AMC排片爬虫任务...")

    weekend_dates = get_weekend_dates()
    logger.info(f"计划抓取日期: {[d.strftime('%Y-%m-%d (%A)') for d in weekend_dates]}")

    if not weekend_dates:
        logger.warning("未找到任何周末日期")
        weekend_dates = [datetime.now() + timedelta(days=5)]

    all_showtimes = {}

    for theater in THEATERS:
        theater_data = {
            'name': theater['name'],
            'dates': {}
        }

        for date_obj in weekend_dates:
            date_str = date_obj.strftime('%Y-%m-%d')
            day_name = date_obj.strftime('%A')

            movies = fetch_showtimes(theater, date_str)

            theater_data['dates'][date_str] = {
                'day': day_name,
                'movies': movies
            }

        all_showtimes[theater['name']] = theater_data

    # 保存排片数据
    output_dir = 'data'
    os.makedirs(output_dir, exist_ok=True)

    output_file = os.path.join(output_dir, 'showtimes.json')
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(all_showtimes, f, ensure_ascii=False, indent=2)
    logger.info(f"排片数据已保存到 {output_file}")

    # 保存更新时间
    update_file = os.path.join(output_dir, 'last_updated.json')
    with open(update_file, 'w', encoding='utf-8') as f:
        json.dump({
            'timestamp': datetime.now().isoformat(),
            'timezone': 'UTC'
        }, f, ensure_ascii=False, indent=2)
    logger.info(f"更新时间已保存到 {update_file}")
    logger.info("爬虫任务完成！")


if __name__ == '__main__':
    main()
