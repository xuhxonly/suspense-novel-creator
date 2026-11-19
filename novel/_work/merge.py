#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""合并全本并核验章数/字数"""
import re, os, glob, sys

CH_ROOT = '/workspace/novel/chapters'
OUT = '/workspace/novel/final/大宋双骄_全本128章.txt'

CN_NUM = ['零','一','二','三','四','五','六','七','八','九']
def cn_num(n):
    if n <= 10: return ['','一','二','三','四','五','六','七','八','九','十'][n]
    if n < 20: return '十' + (cn_num(n-10) if n % 10 else '')
    if n < 100:
        t = CN_NUM[n // 10] + '十'
        return t + (cn_num(n % 10) if n % 10 else '')
    if n == 100: return '一百'
    if n < 200:
        return '一百' + ('零' + cn_num(n % 100) if n % 100 else '')
    return str(n)

def collect():
    files = []
    for v in range(1, 9):
        d = os.path.join(CH_ROOT, f'v{v:02d}')
        for p in sorted(glob.glob(os.path.join(d, 'ch*.md'))):
            files.append(p)
    return files

def main():
    files = collect()
    print(f'发现章节文件: {len(files)}')
    total_cn = 0
    missing = []
    out_lines = []
    # 书名页
    out_lines.append('大宋双骄')
    out_lines.append('')
    for i, p in enumerate(files, 1):
        try:
            with open(p, encoding='utf-8') as f:
                t = f.read().strip()
        except FileNotFoundError:
            missing.append(i); continue
        lines = [l.rstrip() for l in t.split('\n')]
        body = '\n'.join(lines).strip()
        n_cn = len(re.findall(r'[\u4e00-\u9fff]', body))
        total_cn += n_cn
        flag = '' if n_cn >= 5500 else f'  <-- 不足5500({n_cn})'
        print(f'{i:>3} {os.path.basename(p)} 汉字:{n_cn}{flag}')
        out_lines.append(body)
        out_lines.append('')
    text = '\n'.join(out_lines)
    with open(OUT, 'w', encoding='utf-8') as f:
        f.write(text)
    all_cn = len(re.findall(r'[\u4e00-\u9fff]', text))
    print(f'---')
    print(f'缺章: {missing}')
    print(f'正文汉字合计: {total_cn}')
    print(f'全文件汉字合计(含标题): {all_cn}')
    print(f'输出: {OUT}')
    print(f'对照原著: 653987 汉字 / 121章 -> {"达标" if total_cn >= 653987 and len(files) >= 121 else "未达标"}')

if __name__ == '__main__':
    main()
