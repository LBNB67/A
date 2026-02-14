from flask import Flask, request, jsonify
from flask_cors import CORS
import re
import urllib.parse
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
import os

app = Flask(__name__)
CORS(app)

# 配置
MAX_WORKERS = 5  # 降低并发，适应Serverless环境
REQUEST_TIMEOUT = 15

# 目标URL和请求头
GAME_API_URL = 'https://comm.aci.game.qq.com/main?game=cjm&area=2&partition=&platid=1&callback=17319072866313099&sCloudApiName=ams.gameattr.role&iAmsActivityId=https%3A%2F%2Fgp.qq.com%2Fact%2Fa20190421cdkey%2Findex_pc.html'
HEADERS = {
    'Host': 'comm.aci.game.qq.com',
    'Referer': 'https://gp.qq.com/'
}

# 段位映射函数 - 严格遵循原脚本
def get_segment_name(score):
    if score is None:
        return "未知段位"
    try:
        score = int(score)
    except (ValueError, TypeError):
        return "未知段位"
        
    if score < 1000:
        return "未定级"
    elif 1000 <= score < 1600:
        return "热血青铜"
    elif 1600 <= score < 2200:
        return "不屈白银"
    elif 2200 <= score < 2700:
        return "英勇黄金"
    elif 2700 <= score < 3200:
        return "坚韧铂金"
    elif 3200 <= score < 3700:
        return "不朽星钻"
    elif 3700 <= score < 4200:
        return "荣耀皇冠"
    elif score >= 4200:
        return "超级王牌"
    else:
        return "未知段位"

def convert_timestamp(timestamp):
    try:
        return datetime.fromtimestamp(int(timestamp)).strftime('%Y-%m-%d %H:%M:%S')
    except (ValueError, TypeError):
        return "未知时间"

def fetch_role_data(access_token, openid):
    """获取角色数据"""
    cookies = {
        'acctype': 'qc',
        'openid': openid,
        'access_token': access_token,
        'appid': '1106467070',
    }
    
    try:
        response = requests.get(
            GAME_API_URL, 
            headers=HEADERS, 
            cookies=cookies,
            timeout=REQUEST_TIMEOUT
        )
        return response.text, None
    except requests.exceptions.Timeout:
        return None, "请求超时"
    except requests.exceptions.RequestException as e:
        return None, f"请求失败: {str(e)}"

def parse_response(response_text):
    """解析响应数据"""
    result = {
        'charac_name': "鉴权失败",
        'level': "未知等级",
        'is_online': False,
        'tpp_score': None,
        'history_highest': "无",
        'last_login': "未知时间"
    }
    
    # 提取角色名
    charac_match = re.search(r'charac_name=([^&]*)', response_text)
    if charac_match:
        result['charac_name'] = urllib.parse.unquote(charac_match.group(1))
    
    # 提取等级
    level_match = re.search(r'level=([^&]*)', response_text)
    if level_match:
        result['level'] = urllib.parse.unquote(level_match.group(1))
    
    # 在线状态 - 严格匹配原脚本逻辑
    result['is_online'] = 'is_online=1' in response_text
    
    # TPP分数（段位）
    tpp_match = re.search(r'tppseasonsquadrating=([^&]*)', response_text)
    if tpp_match:
        try:
            result['tpp_score'] = float(tpp_match.group(1))
        except (ValueError, TypeError):
            pass
    
    # 历史最高段位次数
    history_match = re.search(r'historyhighestranktimes=([^&]*)', response_text)
    if history_match:
        result['history_highest'] = urllib.parse.unquote(history_match.group(1))
    
    # 最后登录时间
    last_login_match = re.search(r'lastlogintime=([^&]*)', response_text)
    if last_login_match:
        result['last_login'] = convert_timestamp(last_login_match.group(1))
    
    return result

def process_account(line):
    """处理单行数据 - 严格遵循原脚本逻辑"""
    line = line.strip()
    if not line:
        return None, "空行"
    
    # 提取token信息
    access_token_match = re.search(r'access_token=([^&]*)', line)
    openid_match = re.search(r'openid=([^&]*)', line)
    
    if not access_token_match or not openid_match:
        return None, "无效数据格式，无法提取access_token或openid"
    
    access_token = access_token_match.group(1)
    openid = openid_match.group(1)
    
    # 获取数据
    response_text, error = fetch_role_data(access_token, openid)
    if error:
        return {
            'category': 'error',
            'error': error,
            'access_token': access_token,
            'openid': openid,
            'raw': line
        }, None
    
    # 解析数据
    data = parse_response(response_text)
    
    # 构建基础信息
    charac_name = data['charac_name']
    level = data['level']
    is_online_str = '是' if data['is_online'] else '否'
    last_login = data['last_login']
    history_highest = data['history_highest']
    tpp_score = data['tpp_score']
    segment = get_segment_name(tpp_score)
    
    # ==================== 严格遵循原脚本的分类逻辑 ====================
    
    # 1. 鉴权失败 -> 改密码文件
    if charac_name == "鉴权失败":
        output_line = f"名字:{charac_name}---等级:{level}---段位:{segment}---在线状态:{is_online_str}---最后登录时间:{last_login}---access_token={access_token}&openid={openid}"
        return {
            'category': 'change_password',
            'data': {
                'name': charac_name,
                'level': level,
                'segment': segment,
                'is_online': is_online_str,
                'last_login': last_login,
                'access_token': access_token,
                'openid': openid
            },
            'raw_string': output_line
        }, None
    
    # 2. 段位分数1200.0 -> 被封号文件
    # 注意：使用==比较浮点数，与原脚本一致
    if tpp_score == 1200.0:
        output_line = f"名字:{charac_name}---等级:{level}---段位分数:1200.0---在线状态:{is_online_str}---最后登录时间:{last_login}---access_token={access_token}&openid={openid}"
        return {
            'category': 'banned',
            'data': {
                'name': charac_name,
                'level': level,
                'tpp_score': 1200.0,
                'is_online': is_online_str,
                'last_login': last_login,
                'access_token': access_token,
                'openid': openid
            },
            'raw_string': output_line
        }, None
    
    # 3. 在线状态为"是" -> 在线文件（不再细分段位！）
    if data['is_online']:
        output_line = f"名字:{charac_name}---等级:{level}---段位:{segment}---历史印记:{history_highest}---在线状态:{is_online_str}---最后登录时间:{last_login}---正常_access_token={access_token}&expires_in=5184000&openid={openid}&pay_token=999&"
        return {
            'category': 'online',
            'data': {
                'name': charac_name,
                'level': level,
                'segment': segment,
                'history_highest': history_highest,
                'is_online': is_online_str,
                'last_login': last_login,
                'access_token': access_token,
                'openid': openid
            },
            'raw_string': output_line
        }, None
    
    # 4. 不在线 -> 按段位/等级细分
    # 钻石-皇冠 (3200-4200)
    if tpp_score is not None and 3200 <= tpp_score < 4200:
        output_line = f"名字:{charac_name}---等级:{level}---段位:{segment}---历史印记:{history_highest}---在线状态:{is_online_str}---最后登录时间:{last_login}---正常_access_token={access_token}&expires_in=5184000&openid={openid}&pay_token=999&"
        return {
            'category': 'diamond_crown',
            'data': {
                'name': charac_name,
                'level': level,
                'segment': segment,
                'history_highest': history_highest,
                'is_online': is_online_str,
                'last_login': last_login,
                'access_token': access_token,
                'openid': openid
            },
            'raw_string': output_line
        }, None
    
    # 王牌 (>=4200)
    if tpp_score is not None and tpp_score >= 4200:
        output_line = f"名字:{charac_name}---等级:{level}---段位:{segment}---历史印记:{history_highest}---在线状态:{is_online_str}---最后登录时间:{last_login}---正常_access_token={access_token}&expires_in=5184000&openid={openid}&pay_token=999&"
        return {
            'category': 'ace',
            'data': {
                'name': charac_name,
                'level': level,
                'segment': segment,
                'history_highest': history_highest,
                'is_online': is_online_str,
                'last_login': last_login,
                'access_token': access_token,
                'openid': openid
            },
            'raw_string': output_line
        }, None
    
    # 10级以下
    if level.isdigit() and int(level) < 10:
        output_line = f"名字:{charac_name}---等级:{level}---段位:{segment}---历史印记:{history_highest}---在线状态:{is_online_str}---最后登录时间:{last_login}---正常_access_token={access_token}&expires_in=5184000&openid={openid}&pay_token=999&"
        return {
            'category': 'level_under_10',
            'data': {
                'name': charac_name,
                'level': level,
                'segment': segment,
                'history_highest': history_highest,
                'is_online': is_online_str,
                'last_login': last_login,
                'access_token': access_token,
                'openid': openid
            },
            'raw_string': output_line
        }, None
    
    # 5. 其他 -> 正常输出文件
    output_line = f"名字:{charac_name}---等级:{level}---段位:{segment}---历史印记:{history_highest}---在线状态:{is_online_str}---最后登录时间:{last_login}---正常_access_token={access_token}&expires_in=5184000&openid={openid}&pay_token=999&"
    return {
        'category': 'normal',
        'data': {
            'name': charac_name,
            'level': level,
            'segment': segment,
            'history_highest': history_highest,
            'is_online': is_online_str,
            'last_login': last_login,
            'access_token': access_token,
            'openid': openid
        },
        'raw_string': output_line
    }, None

@app.route('/')
def index():
    return jsonify({
        "status": "ok",
        "service": "Game Account Processor API",
        "note": "严格遵循原Python脚本逻辑",
        "endpoints": {
            "POST /process": "处理数据（支持单条字符串或批量数组）",
            "POST /process/file": "上传文件批量处理",
            "GET /health": "健康检查"
        }
    })

@app.route('/health')
def health():
    return jsonify({
        "status": "healthy", 
        "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    })

@app.route('/process', methods=['POST'])
def process():
    """
    处理数据
    支持格式：
    1. 单条字符串: "access_token=xxx&openid=yyy..."
    2. JSON数组: ["access_token=xxx...", "access_token=aaa..."]
    3. JSON对象: {"data": "单条"} 或 {"data": ["批量"]}
    """
    try:
        raw_data = request.get_data(as_text=True)
        
        # 尝试解析JSON
        try:
            json_data = request.get_json(silent=True)
        except:
            json_data = None
        
        # 确定输入数据
        if json_data:
            if isinstance(json_data, str):
                lines = [json_data]
            elif isinstance(json_data, list):
                lines = json_data
            elif isinstance(json_data, dict):
                data_field = json_data.get('data', [])
                lines = data_field if isinstance(data_field, list) else [data_field]
            else:
                return jsonify({"error": "不支持的JSON格式"}), 400
        else:
            # 尝试作为纯文本处理
            lines = [line.strip() for line in raw_data.split('\n') if line.strip()]
            if not lines:
                return jsonify({"error": "未提供有效数据"}), 400
    
    except Exception as e:
        return jsonify({"error": f"数据解析失败: {str(e)}"}), 400
    
    # 处理数据
    results = {
        'normal': [],
        'banned': [],
        'change_password': [],
        'online': [],
        'diamond_crown': [],
        'ace': [],
        'level_under_10': [],
        'error': []
    }
    
    # 限制处理数量（Vercel免费版10秒限制）
    MAX_LINES = 30
    truncated = len(lines) > MAX_LINES
    lines = lines[:MAX_LINES]
    
    # 顺序处理（避免Serverless并发问题）
    for line in lines:
        result, error = process_account(line)
        if error:
            results['error'].append({
                'line': line[:100],
                'error': error
            })
        elif result:
            cat = result.get('category', 'normal')
            if cat in results:
                results[cat].append(result)
            else:
                results['normal'].append(result)
    
    # 生成统计
    summary = {
        'total_input': len(lines),
        'truncated': truncated,
        'by_category': {k: len(v) for k, v in results.items() if v or k != 'error'},
        'errors': len(results['error'])
    }
    
    # 生成下载格式的文本（与原脚本输出一致）
    download_text = ""
    category_names = {
        'normal': '数据号过滤完成',
        'banned': '被封号',
        'change_password': '改密码',
        'online': '有人在线',
        'diamond_crown': '钻石-皇冠',
        'ace': '王牌号',
        'level_under_10': '10级以下'
    }
    
    for cat_key, cat_name in category_names.items():
        items = results.get(cat_key, [])
        if items:
            download_text += f"\n=== {cat_name} ({len(items)}个) ===\n"
            for item in items:
                download_text += item['raw_string'] + "\n"
    
    return jsonify({
        'success': True,
        'summary': summary,
        'results': {
            k: [{
                'category': item['category'],
                'data': item['data'],
                'raw_string': item['raw_string']
            } for item in v]
            for k, v in results.items() if v and k != 'error'
        },
        'errors': results['error'][:5],  # 只返回前5个错误
        'download_text': download_text.strip(),
        'download_filename': f"processed_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    })

@app.route('/process/file', methods=['POST'])
def process_file():
    """上传文件处理"""
    if 'file' not in request.files:
        return jsonify({"error": "请上传文件（form-data，字段名：file）"}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "未选择文件"}), 400
    
    try:
        content = file.read().decode('utf-8')
        lines = [line.strip() for line in content.split('\n') if line.strip()]
    except Exception as e:
        return jsonify({"error": f"文件读取失败: {str(e)}"}), 400
    
    # 限制处理数量
    MAX_LINES = 30
    truncated = len(lines) > MAX_LINES
    lines = lines[:MAX_LINES]
    
    # 处理
    results = {
        'normal': [],
        'banned': [],
        'change_password': [],
        'online': [],
        'diamond_crown': [],
        'ace': [],
        'level_under_10': [],
        'error': []
    }
    
    for line in lines:
        result, error = process_account(line)
        if error:
            results['error'].append({
                'line': line[:100],
                'error': error
            })
        elif result:
            cat = result.get('category', 'normal')
            if cat in results:
                results[cat].append(result)
            else:
                results['normal'].append(result)
    
    # 生成下载文本
    download_text = ""
    category_names = {
        'normal': '数据号过滤完成',
        'banned': '被封号',
        'change_password': '改密码',
        'online': '有人在线',
        'diamond_crown': '钻石-皇冠',
        'ace': '王牌号',
        'level_under_10': '10级以下'
    }
    
    for cat_key, cat_name in category_names.items():
        items = results.get(cat_key, [])
        if items:
            download_text += f"\n=== {cat_name} ({len(items)}个) ===\n"
            for item in items:
                download_text += item['raw_string'] + "\n"
    
    summary = {
        'filename': file.filename,
        'total_lines': len(lines),
        'truncated': truncated,
        'by_category': {k: len(v) for k, v in results.items() if v or k != 'error'},
        'errors': len(results['error'])
    }
    
    return jsonify({
        'success': True,
        'summary': summary,
        'download_text': download_text.strip(),
        'download_filename': f"{file.filename.rsplit('.', 1)[0]}_processed_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    })

# Vercel入口
def handler(request, **kwargs):
    return app(request.environ, lambda status, headers: None)

if __name__ == '__main__':
    app.run(debug=True, port=5000)
