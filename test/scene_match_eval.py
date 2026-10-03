#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# @Time    : 2026/09/26
# @Author  : Jerry
# @File    : scene_match_eval.py
# @Description: 场景匹配评测脚本：跑评测集，输出分组指标与明细报告。
#
# 用法:
#   python test/scene_match_eval.py                          # 默认 bge-small-zh-v1.5 + 配置阈值
#   python test/scene_match_eval.py --model shibing624/text2vec-base-chinese
#   python test/scene_match_eval.py --threshold 0.40 --report 报告名
#
# 输出:
#   - 控制台: 总体准确率 / 分组准确率 / 混淆明细
#   - test/scene_match_report_<model别名>.md: 完整报告（可归档对比）

import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT_DIR = Path(__file__).parent.parent
os.environ.setdefault("HF_ENDPOINT", "https://www.modelscope.cn")
os.environ.setdefault("HF_HOME", str(ROOT_DIR / "models/models"))
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
sys.path.insert(0, str(ROOT_DIR))

import numpy as np
import yaml
from sentence_transformers import SentenceTransformer

DEFAULT_MODEL = "BAAI/bge-small-zh-v1.5"
DESC_CONFIG_PATH = ROOT_DIR / "xncagent/config/scene_desc.yaml"
CASES_PATH = ROOT_DIR / "test/scene_match_cases.yaml"
REPORT_DIR = ROOT_DIR / "test"


def load_desc_config() -> dict:
    data = yaml.safe_load(DESC_CONFIG_PATH.read_text(encoding="utf-8"))
    return {
        "threshold": float(data["threshold"]),
        "fallback": data["fallback_scene"],
        "scenes": data["scenes"],  # dict: 场景名 -> 描述
    }


def load_cases() -> list[dict]:
    data = yaml.safe_load(CASES_PATH.read_text(encoding="utf-8"))
    return data["cases"]


def normalize_expected(expected) -> list[str]:
    return expected if isinstance(expected, list) else [expected]


def main() -> int:
    parser = argparse.ArgumentParser(description="场景匹配评测")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="句向量模型名")
    parser.add_argument("--threshold", type=float, default=None, help="兜底阈值，缺省读 scene_desc.yaml")
    parser.add_argument("--report", default=None, help="报告文件名（不含路径），缺省自动生成")
    args = parser.parse_args()

    desc_cfg = load_desc_config()
    threshold = args.threshold if args.threshold is not None else desc_cfg["threshold"]
    fallback = desc_cfg["fallback"]
    scenes: dict[str, str] = desc_cfg["scenes"]
    cases = load_cases()

    if fallback not in scenes:
        print(f"配置错误: fallback_scene '{fallback}' 不在 scenes 中")
        return 1

    print(f"模型: {args.model}")
    print(f"阈值: {threshold}（{'命令行覆盖' if args.threshold is not None else 'scene_desc.yaml'}）")
    print(f"用例数: {len(cases)}，场景数: {len(scenes)}（含兜底 '{fallback}'）")
    print("-" * 60)

    # 预测直接走生产代码 match_scene，保证评测与线上逻辑永远一致；
    # 场景描述向量只用于计算 margin（决策边缘诊断）。
    from xncagent.scene_matcher import match_scene as prod_match_scene, _scene_matrix
    prod_names, prod_emb = _scene_matrix()

    model = SentenceTransformer(args.model, device="cpu")

    # 逐条推理
    rows = []
    for case in cases:
        top_name, top_score = prod_match_scene(case["query"])
        q_emb = model.encode([case["query"]], normalize_embeddings=True)
        sims = (q_emb @ prod_emb.T)[0]
        order = np.argsort(-sims)
        margin = float(sims[order[0]] - sims[order[1]])
        # 阈值兜底：最高分不足则判为无关闲聊
        predicted = top_name if top_score >= threshold else fallback
        expected_list = normalize_expected(case["expected"])
        hit = predicted in expected_list
        rows.append({
            "query": case["query"],
            "expected": " / ".join(expected_list),
            "predicted": predicted,
            "hit": hit,
            "category": case["category"],
            "note": case.get("note", ""),
            "top_name": top_name,
            "top_score": top_score,
            "margin": margin,
            "fell_to_fallback": predicted == fallback and fallback not in expected_list,
        })

    # 汇总指标
    total = len(rows)
    hits = sum(1 for r in rows if r["hit"])
    by_cat = defaultdict(lambda: [0, 0])  # category -> [hit, total]
    confusion = defaultdict(int)          # (expected, predicted) -> count，只记错误
    fallback_missed = []                  # 期望兜底但没兜住的
    for r in rows:
        by_cat[r["category"]][0] += int(r["hit"])
        by_cat[r["category"]][1] += 1
        if not r["hit"]:
            confusion[(r["expected"], r["predicted"])] += 1
            if fallback not in r["expected"].split(" / ") and r["predicted"] != fallback:
                fallback_missed.append(r)
        if r["fell_to_fallback"]:
            confusion[(r["expected"], f"误判兜底({r['top_name']} {r['top_score']:.2f})")] += 1

    print(f"总体准确率: {hits}/{total} = {hits/total:.1%}")
    print("\n分组指标:")
    for cat in sorted(by_cat):
        h, t = by_cat[cat]
        print(f"  {cat:<16} {h}/{t} = {h/t:.0%}")

    errors = [r for r in rows if not r["hit"]]
    print(f"\n错误明细 ({len(errors)} 条):")
    for r in errors:
        flag = " [误判兜底]" if r["fell_to_fallback"] else ""
        print(f"  ✗ {r['query']}  期望[{r['expected']}] 实际[{r['predicted']}]"
              f"  top={r['top_name']}({r['top_score']:.3f}) 分差={r['margin']:.3f}{flag}")
        if r["note"]:
            print(f"    note: {r['note']}")

    # 写报告文件
    model_alias = args.model.replace("/", "_")
    report_path = REPORT_DIR / (args.report or f"scene_match_report_{model_alias}.md")
    lines = [
        f"# 场景匹配评测报告",
        f"",
        f"- 时间: {datetime.now().isoformat(timespec='seconds')}",
        f"- 模型: `{args.model}`",
        f"- 阈值: {threshold}",
        f"- 用例集: `{CASES_PATH.name}`（{total} 条）",
        f"- 总体准确率: **{hits/total:.1%}**（{hits}/{total}）",
        f"",
        f"## 分组指标",
        f"",
        f"| 类别 | 命中/总数 | 准确率 |",
        f"| --- | --- | --- |",
    ]
    for cat in sorted(by_cat):
        h, t = by_cat[cat]
        lines.append(f"| {cat} | {h}/{t} | {h/t:.0%} |")
    lines += [f"", f"## 错误明细", f""]
    if errors:
        lines.append("| 输入 | 期望 | 实际 | top1(分数) | 分差 | 备注 |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for r in errors:
            lines.append(
                f"| {r['query']} | {r['expected']} | {r['predicted']} "
                f"| {r['top_name']}({r['top_score']:.3f}) | {r['margin']:.3f} | {r['note']} |"
            )
    else:
        lines.append("无错误，可以考虑扩充用例集或下调阈值收紧兜底。")
    lines += [
        f"",
        f"## 全部用例分数",
        f"",
        f"| 输入 | 期望 | 预测 | top1 | 分差 | 命中 |",
        f"| --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        mark = "✓" if r["hit"] else "✗"
        lines.append(
            f"| {r['query']} | {r['expected']} | {r['predicted']} "
            f"| {r['top_name']}({r['top_score']:.3f}) | {r['margin']:.3f} | {mark} |"
        )
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n报告已写入: {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
