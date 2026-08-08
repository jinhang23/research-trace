---
id: 002c
parent: 001
status: done
title: 数据增强：回译（英→德→英）
date: 2026-03-08
commit: e7f8a9b
author: agent:claude
tags: augmentation
path: /blue/nlp-lab/jdoe/data/agnews-backtrans | 回译增强后的训练集，240 MB
path: https://huggingface.co/Helsinki-NLP/opus-mt-en-de | 回译用的翻译模型
---

## 为什么
另一条独立的路：不动模型，只把训练数据变多。假设：回译产生的同义改写能让
基线模型学到更稳的决策边界，提升幅度可以和换模型相加。

## 做了什么
- 用 opus-mt 英→德→英，对训练集做一遍，得到等量的改写样本
- 原样本 + 增强样本一起喂给基线模型

## 结果
准确率 0.912（基线 0.897）。人工抽查 100 条回译结果，约 8% 语义明显漂移。

## 结论
有效但幅度有限（+1.5 点），且远不如换模型（+4.6 点）。增益来源和 [[002]]
不同，理论上可以叠加。

## 下一步
和 DistilBERT 合起来试。
