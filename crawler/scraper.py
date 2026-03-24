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
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter

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
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate, br',
    'Cache-Control': 'max-age=0',
    'Referer': 'https://www.amctheatres.com/',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'same-origin',
}

def create_session():
    """创建带有重试机制的requests session"""
    session = requests.Session()

    # 禁用代理（解决ProxyError问题）
    session.trust_env = False

    # 添加重试策略（3次重试）
    retry_strategy = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=['GET']
    )

    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount('http://', adapter)
    session.mount('https://', adapter)

    return session


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


def fetch_showtimes(theater, date_str, session=None):
    """
    抓取指定影厅和日期的排片信息
    从 RSC Payload 中用正则提取电影和场次数据
    """
    if session is None:
        session = create_session()

    url = f"{theater['url']}{date_str}"
    logger.info(f"抓取 {theater['name']} {date_str} -> {url}")

    try:
        # 使用session发送请求，禁用代理，增加超时
        resp = session.get(
            url,
            headers=HEADERS,
            timeout=15,
            proxies={},  # 显式禁用代理
            allow_redirects=True,
            verify=True
        )
        resp.raise_for_status()
        html = resp.text
        logger.info(f"获取到 HTML，长度: {len(html)}")

        # 诊断1: 搜索 Next.js RSC payload 格式 (__next_f)
        next_f = re.findall(r'self\.__next_f\.push\(\[.*?\]\)', html[:200000])
        logger.info(f"  [DEBUG] '__next_f' 出现: {len(next_f)} 次")
        if next_f:
            logger.info(f"  [DEBUG] 第一个: {next_f[0][:300]}")

        # 诊断2: 搜索 option 下拉菜单里的电影 (这个之前确认存在)
        option_movies = re.findall(r'<option value="([^"]+)">([^<]+)</option>', html)
        logger.info(f"  [DEBUG] <option> 电影选项: {option_movies[:10]}")

        # 诊断3: 全文搜索 script 标签内容（不限50000字符）
        all_scripts = re.findall(r'<script[^>]*>([\s\S]*?)</script>', html)
        non_empty = [(i, s) for i, s in enumerate(all_scripts) if len(s.strip()) > 10]
        logger.info(f"  [DEBUG] 共 {len(all_scripts)} 个script标签, 非空 {len(non_empty)} 个")
        for i, script in non_empty[:5]:
            logger.info(f"  [DEBUG] script[{i}] 长度={len(script)}, 前300字符: {script[:300]}")

        # 诊断4: 搜索HTML中间部分（250KB附近）是否有排片数据
        mid = html[200000:210000]
        for pat_label, pat in [('time/amPm', r'"amPm"'), ('display', r'"display"'), ('next_f', r'__next_f')]:
            if re.search(pat, mid):
                pos = re.search(pat, mid).start()
                logger.info(f"  [DEBUG] 中部({pat_label}): ...{mid[max(0,pos-100):pos+200]}...")
            else:
                logger.info(f"  [DEBUG] 中部无 '{pat_label}'")

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
    从 HTML 中解析电影和场次信息。

    AMC 网站实际 HTML 结构：
    - 每部电影有一个 <div aria-label="Showtimes for [Movie Title]"> 的容器
    - 场次时间在容器内的 <a href="/showtimes/[id]"> 链接的文字中
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, 'html.parser')
    movies = []

    # 找所有电影 section：aria-label 以 "Showtimes for " 开头
    movie_sections = soup.find_all(
        attrs={'aria-label': re.compile(r'^Showtimes for ')}
    )
    logger.info(f"找到 {len(movie_sections)} 个电影 section")

    for section in movie_sections:
        title = section.get('aria-label', '').replace('Showtimes for ', '').strip()
        if not title:
            continue

        # 在该 section 内找所有场次链接 <a href="/showtimes/数字">
        time_links = section.find_all('a', href=re.compile(r'^/showtimes/\d+'))
        times = []
        for a in time_links:
            # 只取第一个直接文本节点，跳过 <span>（如 "UP TO 15% OFF"）
            raw = a.find(string=True, recursive=False)
            if raw:
                t = raw.strip()
                if re.match(r'^\d{1,2}:\d{2}[ap]m$', t):
                    times.append(t)

        logger.info(f"  电影: {title}, 场次: {times}")

        if times:
            movies.append({
                'title': title,
                'showtimes': times
            })

    if not movies:
        logger.warning("未解析到任何电影，请检查 HTML 结构是否变化")

    return movies


def main():
    """主函数"""
    logger.info("开始AMC排片爬虫任务...")

    # 创建会话，复用连接
    session = create_session()

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

            movies = fetch_showtimes(theater, date_str, session)

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

    # 关闭session
    session.close()
    logger.info("爬虫任务完成！")


if __name__ == '__main__':
    main()
