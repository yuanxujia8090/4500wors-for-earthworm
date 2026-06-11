#!/usr/bin/env python3
"""
将 4500words 英文文章转换为累计式词汇表 JSON。

用法:
    python3 convert.py [input.md] [output_dir]

默认:
    input:  01.md (当前目录)
    output: data/
"""

import json
import re
import sys
import urllib.request
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# ========== 配置 ==========
TRANSLATE_URL = "http://localhost:8050/v1/completions"
TRANSLATE_MODEL = "Hy-MT2-1.8B-oQ6"
AUTH_TOKEN = "keykey12345"
CMU_DICT_PATH = "/Users/yuanxj/nltk_data/corpora/cmudict/cmudict"


# ========== IPA 音标 ==========
def load_cmudict(path=CMU_DICT_PATH):
    """加载 CMU 字典，返回 word -> phonemes 映射"""
    dict_map = {}
    with open(path, 'r') as f:
        for line in f:
            parts = line.strip().split()
            if len(parts) >= 3:
                word = parts[0].upper()
                phonemes = ' '.join(parts[2:])
                dict_map[word] = phonemes
    return dict_map

CMU_DICT = load_cmudict()

# ARPABET 到 IPA 的映射
ARPABET_TO_IPA = {
    'AA': 'ɑː', 'AE': 'æ', 'AH': 'ə', 'AO': 'ɔː', 'AW': 'aʊ',
    'AY': 'aɪ', 'B': 'b', 'CH': 'tʃ', 'D': 'd', 'DH': 'ð',
    'EH': 'ɛː', 'ER': 'ɜː', 'EY': 'eɪ', 'F': 'f', 'G': 'ɡ',
    'HH': 'h', 'IH': 'ɪ', 'IY': 'iː', 'JH': 'dʒ', 'K': 'k',
    'L': 'l', 'M': 'm', 'N': 'n', 'NG': 'ŋ', 'OW': 'oʊ',
    'OY': 'ɔɪ', 'P': 'p', 'R': 'r', 'S': 's', 'SH': 'ʃ',
    'T': 't', 'TH': 'θ', 'UH': 'ʊ', 'UW': 'uː', 'V': 'v',
    'W': 'w', 'Y': 'j', 'Z': 'z', 'ZH': 'ʒ',
}

# 重音标记
STRESS_MARKS = {'0': '', '1': 'ˈ', '2': 'ˌ'}


def arpabet_to_ipa(arpabet):
    """将 ARPABET 转换为 IPA"""
    tokens = arpabet.split()
    ipa_tokens = []
    for token in tokens:
        stress = ''
        for i, char in enumerate(token):
            if char.isdigit():
                stress = STRESS_MARKS.get(char, '')
                token = token[:i]
                break
        
        if token in ARPABET_TO_IPA:
            ipa = ARPABET_TO_IPA[token]
            ipa_tokens.append(stress + ipa)
        else:
            ipa_tokens.append(token)
    
    return ''.join(ipa_tokens)


def word_to_ipa(word):
    """将单个单词转换为 IPA 音标字符串"""
    clean = re.sub(r'[^a-zA-Z]', '', word)
    if not clean:
        return ''
    
    upper = clean.upper()
    if upper in CMU_DICT:
        return arpabet_to_ipa(CMU_DICT[upper])
    
    # 尝试去掉末尾 s/es 后查找
    if upper.endswith('S') and upper[:-1] in CMU_DICT:
        return arpabet_to_ipa(CMU_DICT[upper[:-1]])
    if upper.endswith('ES') and upper[:-2] in CMU_DICT:
        return arpabet_to_ipa(CMU_DICT[upper[:-2]])
    
    return ''


def phrase_to_ipa(phrase):
    """将短语转换为 IPA，每个单词音标用 /.../ 表示"""
    words = phrase.split()
    ipa_parts = []
    for word in words:
        ipa = word_to_ipa(word)
        if ipa:
            ipa_parts.append(f'/{ipa}/')
    return ' '.join(ipa_parts)


# ========== 翻译 ==========
_translate_lock = threading.Lock()

def translate_single(text, direction="en2zh"):
    """单句翻译（线程安全）"""
    with _translate_lock:
        url = TRANSLATE_URL
    
        if direction == "en2zh":
            instruction = "翻译为简体中文（仅输出译文内容）："
        else:
            instruction = "Translate to English (output only the translation)："
        
        prompt = f"<｜hy_begin▁of▁sentence｜><｜hy_User｜>{instruction}\n\n{text}<｜hy_place▁holder▁no▁8｜>"
        
        data = {
            "model": TRANSLATE_MODEL,
            "prompt": prompt,
            "temperature": 0,
            "stop": ["<｜hy_place▁holder▁no▁8｜>"]
        }
        
        req = urllib.request.Request(
            url,
            data=json.dumps(data).encode('utf-8'),
            headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {AUTH_TOKEN}'}
        )
        
        try:
            resp = urllib.request.urlopen(req, timeout=60)
            result = json.loads(resp.read().decode('utf-8'))
            if 'choices' in result and len(result['choices']) > 0:
                return result['choices'][0]['text'].strip()
            else:
                return f"[翻译失败]"
        except Exception as e:
            return f"[翻译错误: {e}]"


def translate_batch_parallel(phrases, max_workers=5):
    """并发翻译一批短语"""
    results = [None] * len(phrases)
    
    def translate_with_index(idx, phrase):
        result = translate_single(phrase, "en2zh")
        results[idx] = result
        return idx, result
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(translate_with_index, i, p) for i, p in enumerate(phrases)]
        for future in as_completed(futures):
            future.result()
    
    return results


def translate_batch_sequential(phrases):
    """串行翻译一批短语（fallback）"""
    results = []
    for phrase in phrases:
        result = translate_single(phrase, "en2zh")
        results.append(result)
    return results


# ========== 文章解析 ==========
def parse_article_from_multi(md_path):
    """从多篇文章的 markdown 文件中解析出所有 Passage。
    
    返回: [(passage_num, title, units), ...]
    units 是最小的英文单元列表（按句号/问号/感叹号拆分，长句再按逗号拆分）
    """
    with open(md_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 按 --- 分割段落
    sections = content.split('\n---\n')
    
    # 按 passage 分组
    passages = {}  # {num: {'title': '', 'sections': []}}
    current_passage_num = None
    
    for section in sections:
        lines = section.strip().split('\n')
        
        # 查找 Passage 标题
        passage_match = None
        english_lines = []
        
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            
            # 匹配 Passage N - Title (I/II)
            m = re.match(r'Passage\s+(\d+)\s*-\s*(.+?)(\s*[IIV]+)?$', stripped)
            if m:
                passage_num = int(m.group(1))
                title = m.group(2).strip()
                passage_match = (passage_num, title)
                continue
            
            # 跳过中文标题行
            if re.match(r'^第[一二三四五六七八九十\d]+篇', stripped):
                continue
            
            # 收集英文内容
            english_lines.append(stripped)
        
        if passage_match:
            num, title = passage_match
            if num not in passages:
                passages[num] = {'title': title, 'sections': []}
            passages[num]['sections'].append(' '.join(english_lines))
    
    # 对每个 passage 解析 units
    results = []
    for num in sorted(passages.keys()):
        info = passages[num]
        full_text = ' '.join(info['sections'])
        
        # 按句号/问号/感叹号 + 空格拆分句子
        raw_sentences = re.split(r'[.!?]+(?:\s+)', full_text)
        
        units = []
        for s in raw_sentences:
            s = s.strip()
            if not s:
                continue
            
            # 如果句子中有逗号且长度>40，按逗号拆分
            if ',' in s and len(s) > 40:
                parts = [p.strip() for p in s.split(',') if p.strip()]
                units.extend(parts)
            else:
                # 补上句号（如果原文没有）
                if not s.endswith('.') and not s.endswith('!') and not s.endswith('?'):
                    s = s + '.'
                units.append(s)
        
        results.append((num, info['title'], units))
    
    return results


# ========== 累计式生成 ==========
def generate_cumulative_for_unit(unit):
    """对单个断句生成累计式短语"""
    clean = re.sub(r'[^a-zA-Z\s]', ' ', unit).strip().lower()
    words = clean.split()
    
    if not words:
        return []
    
    result = []
    cumulative = []
    
    for word in words:
        cumulative.append(word)
        
        # 限制最大长度，超过15个单词时截断到12
        if len(cumulative) > 15:
            cumulative = cumulative[-12:]
        
        current_english = ' '.join(cumulative)
        current_ipa = phrase_to_ipa(current_english)
        
        result.append({
            'english': current_english,
            'chinese': '',  # 待翻译填充
            'soundmark': current_ipa
        })
    
    return result


def generate_cumulative(units):
    """将单元列表转换为累计式词汇表"""
    result = []
    for unit in units:
        chunk = generate_cumulative_for_unit(unit)
        result.extend(chunk)
    return result


# ========== 主流程 ==========
def convert_single_article(title, units, output_path, batch_size=100):
    """转换单篇文章"""
    print(f"\n=== 文章: {title} ===")
    
    vocab = generate_cumulative(units)
    print(f"累计短语数: {len(vocab)}")
    
    # 分批并发翻译
    print(f"翻译 ({len(vocab)}个短语)...")
    success_count = 0
    fail_count = 0
    
    for i in range(0, len(vocab), batch_size):
        batch = vocab[i:i+batch_size]
        english_phrases = [item['english'] for item in batch]
        
        print(f"  翻译 {i+1}-{min(i+batch_size, len(vocab))}/{len(vocab)}...")
        translations = translate_batch_sequential(english_phrases)
        
        for j, item in enumerate(batch):
            translation = translations[j]
            item['chinese'] = translation
            
            if not translation.startswith('[翻译') and not translation.startswith('[翻译错误'):
                success_count += 1
            else:
                fail_count += 1
    
    print(f"  成功: {success_count}, 失败: {fail_count}")
    
    # 保存
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(vocab, f, ensure_ascii=False, indent='\t')
    
    print(f"已保存: {output_path}")


def convert_multi_articles(input_path, output_dir):
    """从多篇文章文件中转换所有 Passage"""
    print(f"=== 解析文件: {input_path} ===")
    passages = parse_article_from_multi(input_path)
    print(f"共找到 {len(passages)} 篇 Passage")
    
    for num, title, units in passages:
        # 编号 01~15
        filename = f"{num:02d}.json"
        output_path = Path(output_dir) / filename
        
        try:
            convert_single_article(title, units, output_path)
        except Exception as e:
            print(f"  [错误] Passage {num}: {e}")


if __name__ == '__main__':
    if len(sys.argv) > 1:
        input_file = sys.argv[1]
    else:
        input_file = '15篇文章学会四级4500词文章.md'
    
    if len(sys.argv) > 2:
        output_dir = sys.argv[2]
    else:
        output_dir = 'courses'
    
    convert_multi_articles(input_file, output_dir)
