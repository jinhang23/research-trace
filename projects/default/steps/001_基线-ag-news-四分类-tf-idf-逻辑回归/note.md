---
id: 001
status: done
title: 基线：AG News 四分类，TF-IDF + 逻辑回归
date: 2026-03-02
commit: 1a2b3c4
author: human
tags: baseline
path: /blue/nlp-lab/jdoe/data/agnews-raw | 原始数据集，120 MB，官方 train/test 划分
path: https://github.com/nlp-lab/agnews-cls/tree/1a2b3c4 | 跑这一步的代码
---

## 为什么
先要一个能跑通、数字可信的起点。没有基线，后面所有「提升了多少」都是空话。
选最简单的组合，是为了让后面每一步的增量都能归因到一个具体改动上。

## 做了什么
- `sklearn` TF-IDF（1-2 gram，max_features=50000）+ LogisticRegression
- 用数据集自带的 train/test 划分，不做任何调参

```bash
python train.py --model tfidf-lr --seed 0
```

## 结果
测试集准确率 **0.897**，宏平均 F1 0.896。单次训练 40 秒。

## 结论
基线可信，速度快到可以随便重跑。可以在此之上做改动。

## 下一步
三个方向并行试：换预训练模型；换传统模型但加强特征；做数据增强。
