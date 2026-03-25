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
from zoneinfo import ZoneInfo
from dateutil.easter import easter
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 洛杉矶时区配置（自动处理夏令时）
LA_TZ = ZoneInfo('America/Los_Angeles')

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


def get_nth_weekday(year, month, n, weekday):
    """
    获取指定月份的第n个特定工作日
    weekday: 0=Monday, 6=Sunday
    """
    from datetime import date
    d = date(year, month, 1)
    # 找到第一个该工作日
    while d.weekday() != weekday:
        d += timedelta(days=1)
    # 加上 (n-1)*7 天得到第n个
    d += timedelta(weeks=n-1)
    return d


def get_last_weekday(year, month, weekday):
    """获取指定月份的最后一个特定工作日"""
    from datetime import date
    # 从月末开始往回找
    if month == 12:
        next_month_first = date(year + 1, 1, 1)
    else:
        next_month_first = date(year, month + 1, 1)
    d = next_month_first - timedelta(days=1)
    while d.weekday() != weekday:
        d -= timedelta(days=1)
    return d


def get_holiday_name(date_obj):
    """
    检查给定日期是否是节假日，返回节假日名称或None
    所有动态节假日都基于洛杉矶时区计算
    """
    year = date_obj.year
    month = date_obj.month
    day = date_obj.day

    # 固定日期节假日
    holidays = {
        (1, 1): 'New Year\'s Day',
        (6, 19): 'Juneteenth',
        (7, 4): 'Independence Day',
        (12, 24): 'Christmas Eve',
        (12, 25): 'Christmas Day'
    }

    if (month, day) in holidays:
        return holidays[(month, day)]

    # 动态节假日
    # President's Day: 2月第3个星期一
    pres_day = get_nth_weekday(year, 2, 3, 0)  # 0=Monday
    if date_obj.date() == pres_day:
        return 'President\'s Day'

    # Good Friday: 复活节前2天
    good_friday = easter(year) - timedelta(days=2)
    if date_obj.date() == good_friday:
        return 'Good Friday'

    # Memorial Day: 5月最后一个星期一
    mem_day = get_last_weekday(year, 5, 0)  # 0=Monday
    if date_obj.date() == mem_day:
        return 'Memorial Day'

    # Labor Day: 9月第1个星期一
    labor_day = get_nth_weekday(year, 9, 1, 0)  # 0=Monday
    if date_obj.date() == labor_day:
        return 'Labor Day'

    # Thanksgiving: 11月第4个星期四
    thanks_day = get_nth_weekday(year, 11, 4, 3)  # 3=Thursday
    if date_obj.date() == thanks_day:
        return 'Thanksgiving Day'

    # Day After Thanksgiving
    if date_obj.date() == thanks_day + timedelta(days=1):
        return 'Day After Thanksgiving'

    return None


def get_weekend_dates():
    """
    获取未来8周内需要爬取的所有日期：周末日期 + 节假日
    使用洛杉矶时区，自动处理夏令时和动态节假日（如复活节、感恩节等）
    """
    today = datetime.now(LA_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    showtimes_dates = []
    seen_dates = set()

    for i in range(56):
        check_date = today + timedelta(days=i)
        date_key = check_date.date()

        # 检查是周末
        is_weekend = check_date.weekday() in [5, 6]

        # 检查是节假日
        holiday_name = get_holiday_name(check_date)
        is_holiday = holiday_name is not None

        # 如果是周末或节假日，且未添加过，就加入
        if (is_weekend or is_holiday) and date_key not in seen_dates:
            showtimes_dates.append(check_date)
            seen_dates.add(date_key)
            if is_holiday:
                logger.info(f"  [节假日] {check_date.strftime('%Y-%m-%d')} - {holiday_name}")

    return showtimes_dates


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
      IMAX有排片时：会出现 'imaxwithlaseratamc-' 或 'imax70mm-' （带连字符，表示具体场次条目）
      Dolby有排片时：会出现 'dolbycinemaatamcprime-' （带连字符，表示具体场次条目，而非URL参数）
    如果这些标识不存在，说明页面显示的是fallback内容（其他格式的排片），应返回False。
    """
    if not format_name:
        return True

    html_lower = html.lower()

    if format_name == 'IMAX':
        # 支持两种IMAX格式：普通IMAX和70MM IMAX
        has_imax_laser = 'imaxwithlaseratamc-' in html_lower
        has_imax_70mm = 'imax70mm-' in html_lower

        if has_imax_laser:
            logger.info("  [FORMAT] 找到IMAX场次标识 'imaxwithlaseratamc-'，确认有IMAX排片")
            return True
        elif has_imax_70mm:
            logger.info("  [FORMAT] 找到IMAX 70MM场次标识 'imax70mm-'，确认有IMAX 70MM排片")
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

    # 第三步：提取所有场次时间，同时捕获 status 字段以过滤 "ComingSoon"
    # RSC 数据格式: "status":"AlmostFull","display":{"time":"HH:MM","amPm":"am/pm"}
    # "ComingSoon" 对应 AMC 网站的 "AVAILABLE SOON"（不可购票），需排除
    time_pattern = re.compile(
        r'"status"\s*:\s*"(\w+)"[^{]{0,200}?"display"\s*:\s*\{\s*"time"\s*:\s*"(\d{1,2}:\d{2})"\s*,\s*"amPm"\s*:\s*"(am|pm)"\s*\}'
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

    # 找到所有时间出现的位置，同时记录是否 ComingSoon
    # ComingSoon = AMC 网站的 "AVAILABLE SOON"，不可购票但仍需显示（灰色）
    times_found = []       # (time_str, pos, is_coming_soon)
    for match in time_pattern.finditer(normalized):
        status   = match.group(1)
        time_val = match.group(2)
        ampm     = match.group(3)
        pos      = match.start()
        is_cs    = (status == 'ComingSoon')
        times_found.append((f"{time_val}{ampm}", pos, is_cs))
        if is_cs:
            logger.info(f"  [ComingSoon] {time_val}{ampm}")

    purchasable = [t for t in times_found if not t[2]]
    coming_soon = [t for t in times_found if t[2]]
    logger.info(f"找到 {len(slugs_found)} 个电影slug, {len(purchasable)} 个可购票, {len(coming_soon)} 个ComingSoon")

    if not times_found:
        logger.warning("未找到任何场次时间")
        return []

    # 第五步：将每个时间关联到最近的前一个 slug
    # movies_dict: slug -> {'showtimes': [...], 'coming_soon_times': [...]}
    movies_dict = {}
    all_items = []
    for slug, pos in slugs_found:
        all_items.append(('slug', slug, pos, None))
    for time_str, pos, is_cs in times_found:
        all_items.append(('time', time_str, pos, is_cs))
    all_items.sort(key=lambda x: x[2])

    current_slug = None
    for item_type, value, pos, meta in all_items:
        if item_type == 'slug':
            current_slug = value
            if current_slug not in movies_dict:
                movies_dict[current_slug] = {'showtimes': [], 'coming_soon_times': []}
        elif item_type == 'time' and current_slug:
            is_cs = meta
            if is_cs:
                if value not in movies_dict[current_slug]['coming_soon_times']:
                    movies_dict[current_slug]['coming_soon_times'].append(value)
            else:
                if value not in movies_dict[current_slug]['showtimes']:
                    movies_dict[current_slug]['showtimes'].append(value)

    # 第六步：用 <option> 标签中的电影名获取可读标题
    option_titles = {}
    for option in soup.find_all('option'):
        val = option.get('value', '')
        text = option.get_text(strip=True)
        if val and text:
            option_titles[val] = text

    # 转换为输出格式
    movies = []
    for slug, data in movies_dict.items():
        purchasable_times = data['showtimes']
        cs_times          = data['coming_soon_times']

        # 跳过完全没有任何场次的 slug（非电影条目）
        if not purchasable_times and not cs_times:
            continue

        # 优先使用 <option> 中的标题，否则从 slug 转换
        title = option_titles.get(slug)
        if not title:
            clean_slug = re.sub(r'-\d+$', '', slug)
            title = option_titles.get(clean_slug, slug_to_title(clean_slug))

        # 决定最终 showtimes 和 is_coming_soon 标志
        # 若全部场次都是 ComingSoon，保留这些时间并标记
        # 若有可购票场次，只保留可购票场次
        if purchasable_times:
            final_showtimes = purchasable_times
            is_coming_soon  = False
        else:
            # 全部是 ComingSoon
            final_showtimes = cs_times
            is_coming_soon  = True

        # 检测是否是IMAX 70MM格式
        is_70mm = 'imax70mm' in normalized.lower() and slug.lower() in normalized.lower()
        if is_70mm:
            pattern = r'imax70mm[^}]*' + re.escape(slug)
            if not re.search(pattern, normalized.lower()):
                is_70mm = False

        movies.append({
            'title':          title,
            'slug':           slug,
            'showtimes':      final_showtimes,
            'is_coming_soon': is_coming_soon,
            'is_70mm':        is_70mm
        })
        log_msg = f"  电影: {title}, 场次: {final_showtimes}"
        if is_coming_soon:
            log_msg += " [ComingSoon - 不可购票]"
        if is_70mm:
            log_msg += " [IMAX 70MM]"
        logger.info(log_msg)

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
        weekend_dates = [datetime.now(LA_TZ) + timedelta(days=5)]

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
            holiday_name = get_holiday_name(date_obj)

            movies = fetch_showtimes(theater, date_str, session)

            date_data = {
                'day': day_name,
                'movies': movies
            }
            # 如果是节假日，添加节假日标记
            if holiday_name:
                date_data['holiday'] = holiday_name

            theater_data['dates'][date_str] = date_data

        all_showtimes[theater['name']] = theater_data

    # 保存排片数据
    output_dir = 'data'
    os.makedirs(output_dir, exist_ok=True)

    output_file = os.path.join(output_dir, 'showtimes.json')
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(all_showtimes, f, ensure_ascii=False, indent=2)
    logger.info(f"排片数据已保存到 {output_file}")

    # 保存更新时间（洛杉矶时区）
    update_file = os.path.join(output_dir, 'last_updated.json')
    with open(update_file, 'w', encoding='utf-8') as f:
        json.dump({
            'timestamp': datetime.now(LA_TZ).isoformat(),
            'timezone': 'America/Los_Angeles'
        }, f, ensure_ascii=False, indent=2)
    logger.info(f"更新时间已保存到 {update_file}")

    # 关闭session
    session.close()
    logger.info("爬虫任务完成！")


if __name__ == '__main__':
    main()
