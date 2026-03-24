#!/usr/bin/env python3
"""
AMC电影院周末排片爬虫
抓取Century City IMAX、Century City Dolby Cinema、Universal CityWalk IMAX的排片信息
"""

import requests
from bs4 import BeautifulSoup
import json
import logging
from datetime import datetime, timedelta
import os
import sys
import re

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 目标影厅配置
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

def get_weekend_dates():
    """获取未来2周的周末日期（周六和周日）"""
    today = datetime.now()
    weekend_dates = []

    # 计算接下来14天内的所有周末
    for i in range(14):
        check_date = today + timedelta(days=i)
        # 周六=5，周日=6
        if check_date.weekday() in [5, 6]:
            weekend_dates.append(check_date)

    return weekend_dates

def fetch_showtimes(theater, date_str):
    """
    抓取指定影厅和日期的排片信息

    Args:
        theater: 影厅配置字典
        date_str: 日期字符串 (YYYY-MM-DD)

    Returns:
        列表，包含电影信息
    """
    url = f"{theater['url']}{date_str}"

    try:
        logger.info(f"抓取 {theater['name']} {date_str}...")

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }

        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, 'html.parser')

        # 查找电影容器
        movies = []

        # 方案1：查找具体的排片卡片或列表项
        # 尝试多种选择器获取电影信息
        movie_items = soup.find_all('div', {'data-testid': 'showtimeMovieCard'})

        if not movie_items:
            # 方案2：查找其他可能的电影容器
            movie_items = soup.find_all('article', class_='MovieCard')

        if not movie_items:
            # 方案3：查找具有特定属性的容器
            movie_items = soup.find_all(attrs={'data-movie-title': True})

        if not movie_items:
            # 方案4：查找div with specific classes or attributes
            movie_items = soup.find_all('div', class_=re.compile(r'movie|film|showtime', re.I))
            # Filter out containers with 'ad' or 'promo' in class
            movie_items = [m for m in movie_items if not re.search(r'ad|promo|banner|advertisement', m.get('class', ''), re.I)]

        if not movie_items:
            # 方案5：查找包含时间信息的容器
            movie_items = soup.find_all('li', class_=re.compile(r'movie', re.I))

        for item in movie_items:
            try:
                # 提取电影标题
                title_elem = item.find('h2') or item.find('h3') or item.find(attrs={'data-movie-title': True})
                title = title_elem.get_text(strip=True) if title_elem else 'Unknown'

                # 提取场次信息
                times_elem = item.find_all('button', class_='ShowtimeButton')
                if not times_elem:
                    times_elem = item.find_all('a', class_='showtime')
                if not times_elem:
                    times_elem = item.find_all(attrs={'data-showtime': True})

                showtimes = []
                for time_elem in times_elem:
                    time_text = time_elem.get_text(strip=True)
                    if time_text:
                        showtimes.append(time_text)

                if title and showtimes:
                    movies.append({
                        'title': title,
                        'showtimes': showtimes
                    })
                elif title:
                    # 即使没有抓到具体时间，也记录电影标题
                    movies.append({
                        'title': title,
                        'showtimes': []
                    })

            except Exception as e:
                logger.warning(f"处理电影项目时出错: {e}")
                continue

        if not movies:
            # 如果上述方法都失败，尝试更宽泛的HTML解析
            # 在HTML中查找包含时间信息的文本
            logger.warning(f"未能从{theater['name']}获取电影信息，尝试备选方案...")
            movies = extract_fallback_movies(soup)

        logger.info(f"成功获取 {theater['name']} {len(movies)} 部电影")
        return movies

    except requests.RequestException as e:
        logger.error(f"请求失败 ({theater['name']} {date_str}): {e}")
        return []
    except Exception as e:
        logger.error(f"解析失败 ({theater['name']} {date_str}): {e}")
        return []

def extract_fallback_movies(soup):
    """备选方案：尝试从HTML中提取电影信息"""
    movies = []

    # 广告和促销关键词过滤
    ad_keywords = [
        'sign in', 'join', 'reward', 'points', 'free', 'save',
        'offer', 'promotion', 'special', 'deal', 'coupon', 'discount',
        'member', 'card', 'reserve', 'imax', 'dolby', 'premium format',
        'coming soon', 'now showing', 'rated', '©',
        'advertisement', 'ad', 'banner', 'sponsored', 'click here',
        'learn more', 'buy', 'gift', 'concessions'
    ]

    # 查找特定的电影列表容器
    content_area = None

    # 尝试多个容器选择器
    content_area = soup.find('div', {'data-testid': re.compile(r'showtimes|movies', re.I)})
    if not content_area:
        content_area = soup.find('section', class_=re.compile(r'showtime|movie', re.I))
    if not content_area:
        content_area = soup.find('ul', class_=re.compile(r'movie|film', re.I))
    if not content_area:
        # 最后的备选：使用main但会更严格地过滤
        content_area = soup.find('main')

    if content_area:
        # 获取文本但排除特定容器
        # 移除脚本和样式元素
        for script in content_area(['script', 'style']):
            script.decompose()

        text = content_area.get_text()
        lines = [line.strip() for line in text.split('\n') if line.strip()]

        current_movie = None
        for line in lines:
            # 跳过广告/促销内容
            if any(keyword.lower() in line.lower() for keyword in ad_keywords):
                continue

            # 检查是否是时间格式（更严格的正则表达式）
            time_match = re.search(r'\b(1[0-2]|0?[1-9]):[0-5]\d\s*([AP]M|am|pm)\b', line)
            if time_match:
                # 这是一个有效的时间格式
                if current_movie:
                    current_movie['showtimes'].append(line)
            # 检查是否是电影标题候选（不是广告，合理长度）
            elif 0 < len(line) < 100 and not re.search(r'^\d+$', line):
                # 避免只有数字的行
                if current_movie and current_movie['showtimes']:
                    # 只有当当前电影有场次时才保存
                    movies.append(current_movie)
                    current_movie = None
                # 开始新电影
                if current_movie is None or len(current_movie['showtimes']) > 0:
                    current_movie = {
                        'title': line,
                        'showtimes': []
                    }

        # 保存最后一个电影（如果有场次）
        if current_movie and current_movie['showtimes']:
            movies.append(current_movie)

    return movies

def main():
    """主函数"""
    logger.info("开始AMC排片爬虫任务...")

    # 获取周末日期
    weekend_dates = get_weekend_dates()
    logger.info(f"计划抓取日期: {[d.strftime('%Y-%m-%d (%A)') for d in weekend_dates]}")

    if not weekend_dates:
        logger.warning("未找到任何周末日期")
        weekend_dates = [datetime.now() + timedelta(days=5)]  # 备选：抓取5天后的日期

    # 收集所有影厅的排片数据
    all_showtimes = {}

    for theater in THEATERS:
        theater_data = {
            'name': theater['name'],
            'dates': {}
        }

        for date_obj in weekend_dates:
            date_str = date_obj.strftime('%Y-%m-%d')
            day_name = date_obj.strftime('%A')  # 英文星期几

            movies = fetch_showtimes(theater, date_str)

            theater_data['dates'][date_str] = {
                'day': day_name,
                'movies': movies
            }

        all_showtimes[theater['name']] = theater_data

    # 创建输出目录
    output_dir = 'data'
    os.makedirs(output_dir, exist_ok=True)

    # 保存排片数据
    output_file = os.path.join(output_dir, 'showtimes.json')
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(all_showtimes, f, ensure_ascii=False, indent=2)

    logger.info(f"排片数据已保存到 {output_file}")

    # 保存更新时间
    last_updated = {
        'timestamp': datetime.now().isoformat(),
        'timezone': 'UTC'
    }

    update_file = os.path.join(output_dir, 'last_updated.json')
    with open(update_file, 'w', encoding='utf-8') as f:
        json.dump(last_updated, f, ensure_ascii=False, indent=2)

    logger.info(f"更新时间已保存到 {update_file}")
    logger.info("爬虫任务完成！")

if __name__ == '__main__':
    main()
