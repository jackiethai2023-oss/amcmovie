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
    """获取未来6周的周末日期（周六和周日）"""
    today = datetime.now()
    weekend_dates = []
    for i in range(42):
        check_date = today + timedelta(days=i)
        if check_date.weekday() in [5, 6]:  # 周六=5, 周日=6
            weekend_dates.append(check_date)
    return weekend_dates


def slug_to_title(slug):
    """把 movie slug 转为可读标题，如 'project-hail-mary' -> 'Project Hail Mary'"""
    return slug.replace('-', ' ').title()


def extract_format(theater_name):
    """
    从theater名称中提取格式标识符（IMAX或Dolby Cinema）
    """
    if 'IMAX' in theater_name.upper():
        return 'IMAX'
    elif 'DOLBY' in theater_name.upper():
        return 'Dolby'  # 使用简化形式
    return None


def validate_format_in_html(html, format_name):
    """
    验证返回的HTML中是否真的包含所请求格式的排片信息。
    通过检查RSC payload中的格式标识ID来判断：
      IMAX有排片时：会出现 'imaxwithlaseratamc-' （带连字符，表示具体场次条目）
      Dolby有排片时：会出现 'dolbycinemaatamcprime-' （带连字符，表示具体场次条目，而非URL参数）
    如果这些标识不存在，说明页面显示的是fallback内容（其他格式的排片），应返回False。
    """
    if not format_name:
        return True

    html_lower = html.lower()

    if format_name == 'IMAX':
        if 'imaxwithlaseratamc-' in html_lower:
            logger.info("  [FORMAT] 找到IMAX场次标识 'imaxwithlaseratamc-'，确认有IMAX排片")
            return True
        logger.info("  [FORMAT] 未找到IMAX场次标识，该日期无IMAX排片")
        return False

    elif format_name == 'Dolby':
        # 检查带连字符的格式ID（表示真实场次条目，而不是URL参数）
        if 'dolbycinemaatamcprime-' in html_lower:
            logger.info("  [FORMAT] 找到Dolby场次标识 'dolbycinemaatamcprime-'，确认有Dolby排片")
            return True
        logger.info("  [FORMAT] 未找到Dolby场次标识，该日期无Dolby排片")
        return False

    return True


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

        # 验证返回的HTML是否包含请求的格式
        # 从theater['name']中提取格式（IMAX或Dolby Cinema）
        format_name = extract_format(theater['name'])
        if format_name and not validate_format_in_html(html, format_name):
            logger.warning(f"HTML中未找到格式 '{format_name}'，返回空列表以避免误匹配")
            return []

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
    从 HTML 中的 RSC (React Server Components) Payload 解析电影和场次信息。

    AMC 网站是 Next.js 应用，场次时间不在 SSR HTML 的 DOM 中，
    而是在 <script>self.__next_f.push(...)</script> 的 RSC Payload 里。
    Payload 中的 JSON 使用转义引号 (\")，需要先替换为普通引号再匹配。

    RSC Payload 数据结构：
      "display":{"time":"11:30","amPm":"am"}
      "aria-describedby":"project-hail-mary-76779 ..."
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, 'html.parser')

    # 第一步：提取所有 RSC payload script 标签的内容
    rsc_text = ''
    for script in soup.find_all('script'):
        text = script.string or ''
        if '__next_f' in text:
            rsc_text += text + '\n'

    logger.info(f"RSC payload 文本总长度: {len(rsc_text)}")

    if not rsc_text:
        logger.warning("未找到 RSC payload，尝试从整个 HTML 解析")
        rsc_text = html

    # 第二步：将转义引号 \" 替换为普通引号 "
    # RSC payload 中的 JSON 数据使用 \" 作为引号
    normalized = rsc_text.replace('\\"', '"')

    # 第三步：提取所有场次时间
    # 格式: "display":{"time":"HH:MM","amPm":"am/pm"}
    time_pattern = re.compile(
        r'"display"\s*:\s*\{\s*"time"\s*:\s*"(\d{1,2}:\d{2})"\s*,\s*"amPm"\s*:\s*"(am|pm)"\s*\}'
    )

    # 第四步：提取电影 slug（从 aria-describedby 中）
    # 格式: "aria-describedby":"movie-slug-12345 movie-slug-12345-theater ..."
    # 取第一个空格前的 slug 即为电影标识
    slug_pattern = re.compile(
        r'"aria-describedby"\s*:\s*"([a-z0-9][a-z0-9\- ]+?)"'
    )

    def normalize_slug(raw_slug):
        """
        将 aria-describedby 的第一个词标准化为电影唯一标识。
        例如:
          project-hail-mary-76779-details              -> project-hail-mary-76779
          project-hail-mary-76779-amc-century-city-15  -> project-hail-mary-76779
          ready-or-not-2-here-i-come-80592             -> ready-or-not-2-here-i-come-80592
        规则：找第一个 4 位及以上的纯数字词（AMC 电影 ID），截取到该位置（含）。
        这样可以区分电影标题中的短数字（如续集 "2"）和真正的 AMC 电影 ID（5 位数字）。
        """
        parts = raw_slug.split('-')
        for i, part in enumerate(parts):
            if part.isdigit() and len(part) >= 4:
                return '-'.join(parts[:i + 1])
        return raw_slug

    # 找到所有 slug 出现的位置
    slugs_found = []
    for match in slug_pattern.finditer(normalized):
        full_value = match.group(1)
        # 取第一个空格前的部分，再标准化
        raw_slug = full_value.split(' ')[0].strip()
        slug = normalize_slug(raw_slug)
        pos = match.start()
        if len(slug) < 3:
            continue
        # 过滤非电影 slug
        skip_keywords = ['sign-in', 'join', 'reward', 'promo', 'banner',
                         'header', 'footer', 'nav', 'menu', 'modal',
                         'cookie', 'consent', 'stubs', 'a-list', 'osano']
        if any(kw in slug for kw in skip_keywords):
            continue
        slugs_found.append((slug, pos))

    # 找到所有时间出现的位置
    times_found = []
    for match in time_pattern.finditer(normalized):
        time_val = match.group(1)
        ampm = match.group(2)
        pos = match.start()
        times_found.append((f"{time_val}{ampm}", pos))

    logger.info(f"找到 {len(slugs_found)} 个电影slug, {len(times_found)} 个场次时间")

    if not times_found:
        logger.warning("未找到任何场次时间")
        return []

    # 第五步：将每个时间关联到最近的前一个 slug
    movies_dict = {}
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

    # 第六步：用 <option> 标签中的电影名获取可读标题
    option_titles = {}
    for option in soup.find_all('option'):
        val = option.get('value', '')
        text = option.get_text(strip=True)
        if val and text:
            option_titles[val] = text

    # 转换为输出格式
    movies = []
    for slug, showtimes in movies_dict.items():
        if showtimes:
            # 优先使用 <option> 中的标题，否则从 slug 转换
            # slug 可能带数字后缀（如 project-hail-mary-76779），需去掉
            title = option_titles.get(slug)
            if not title:
                # 去掉末尾数字部分
                clean_slug = re.sub(r'-\d+$', '', slug)
                title = option_titles.get(clean_slug, slug_to_title(clean_slug))
            movies.append({
                'title': title,
                'slug': slug,  # 保存电影slug用于购票链接
                'showtimes': showtimes
            })
            logger.info(f"  电影: {title}, 场次: {showtimes}")

    if not movies:
        logger.warning("未解析到任何电影，请检查 HTML 结构是否变化")

    # 检查是否存在"Available Soon"的电影（不可购买）
    # 统计页面中"Available Soon"的出现次数
    available_soon_count = html.lower().count('available soon')

    if available_soon_count > 0:
        logger.warning(f"页面中检测到{available_soon_count}个'Available Soon'标记")

        # 如果某个电影的时间数等于或接近"Available Soon"的出现次数，说明该电影的所有时间都是"Available Soon"
        # 这种情况下，清空该电影的showtimes，让它显示为SOON状态
        filtered_movies = []
        for movie in movies:
            showtimes_count = len(movie.get('showtimes', []))

            # 如果该电影的所有时间都对应"Available Soon"（启发式判断）
            # 标准：电影有多个时间，且数量与页面的"Available Soon"数量接近
            if showtimes_count > 0 and showtimes_count >= available_soon_count * 0.8:
                logger.warning(f"电影'{movie['title']}'的{showtimes_count}个时间可能都是'Available Soon'，清空排片")
                movie['showtimes'] = []

            filtered_movies.append(movie)

        movies = filtered_movies

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
            'url': theater['url'],  # 保存购票URL用于前端跳转
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
