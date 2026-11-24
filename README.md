# bangdori_mediadata_decrypt

《BanG Dream! 少女乐团派对》（`jp.co.craftegg.band`）本地资源索引解析与资源包还原工具集。

针对游戏私有目录 `files/data/` 中的 `AssetBundleInfo` 索引文件和一堆以 sha256 命名的无扩展名文件，本仓库给出完整的格式分析、校验、改名与批量导出方案。

## 结论：资源包并没有加密

排查下来，所谓的“加密”只有三层障眼法，全部可以无损绕过：

| 层 | 实际情况 | 绕过方式 |
|---|---|---|
| 索引文件 | 私有 protobuf，本身就是明文 | 手写 protobuf 解码，schema 已完整还原 |
| 文件命名 | 文件名 = 索引里的 sha256，看不出内容 | 读资源包内部 `AssetBundle.m_Name`，直接得到 `live2d/chara/025_casual` 这种可读路径 |
| Unity 头 | `UnityFS` 头里的 unityVersion / unityRevision 被改成 `5.x.x` / `0.0.0` | 给解析器设一个 fallback 版本即可；实测 2021.3 与 2022.3 解析结果完全一致 |

实测的容器参数：`UnityFS` v8，flags `0x243`（LZ4HC 压缩 + 块表与目录合并 + 块数据 16 字节对齐），**不含 `0x400`（Unity 内置加密 UnityCN 的标志位）**。块表 396 B 经 LZ4 还原为 833 B，69 个数据块逐个解压成功，累计 8.95 MB 解压内容与包内两个节点（563 KB 数据 + 8 MB `.resS`）完全吻合——头、块表、数据块全是明文。

另有一处容易误判的地方：索引里的 `crc32` 是对**解压后内容**算的（已用两个包精确核对），而 `sha256`（即文件名）与本地字节不一致，它标识的是服务端下发/构建态的字节。两者都不影响解析。

## 目录结构

```
bangdori_mediadata_decrypt/
├─ data/
│  └─ AssetBundleInfo.csv        2253 条索引对照表（名称/文件名/版本/分组/CRC/大小/依赖）
├─ scripts/
│  ├─ abi_tool.py                索引解析、校验、改名、容器探测（零依赖）
│  ├─ bangdream_extract.py       资源包批量还原导出（依赖 UnityPy）
│  └─ unityfs.py                 纯手写 UnityFS 解包器（只依赖 lz4，可独立验证）
├─ output/                       15 个资源包的实测导出结果
├─ requirements.txt
├─ LICENSE                       MIT
└─ README.md
```

## AssetBundleInfo 格式

顶层是标准 protobuf，无压缩、无加密：

```proto
message AssetBundleInfo {
  string   version = 1;      // "10.2.0.230"
  repeated Bundle bundles = 2;
}

message Bundle {
  string       name   = 1;
  BundleDetail detail = 2;
}

message BundleDetail {
  string   name    = 1;      // 与 Bundle.name 相同
  string   sha256  = 2;      // 64 位十六进制，同时就是 /data/ 下的文件名
  string   version = 3;      // 内容版本，如 10.2.0.200
  string   group   = 4;      // StartApp / AsNeeded / AdditionalVoice
  uint32   crc32   = 5;      // 未压缩内容的 CRC32
  repeated string deps = 6;  // 依赖的资源包
  uint64   size    = 7;      // 下发文件字节数
}
```

本次样本的统计：目录版本 `10.2.0.230`，2253 条记录；分组 StartApp 1894 / AsNeeded 317 / AdditionalVoice 42；合计 2.87 GiB，中位 557 KB，最大 `sdchara/builddata` 99.2 MiB；按类型 top 为 live2d 565、starshop 327、thumb 235、sound 151、story 140、star3d 109。

## 安装与使用

```bash
pip install -r requirements.txt
```

```bash
# 1) 生成索引对照表
python scripts/abi_tool.py csv --abi AssetBundleInfo -o data/AssetBundleInfo.csv

# 2) 校验本地文件（sha256 / 大小 / CRC）
python scripts/abi_tool.py verify --abi AssetBundleInfo --dir <data目录>

# 3) 按索引把 sha256 文件名还原成可读名字
python scripts/abi_tool.py rename --abi AssetBundleInfo --dir <data目录> --out <输出目录>

# 4) 探测某个包是不是真的加密了
python scripts/abi_tool.py probe <某个资源包>

# 5) 批量导出资源（推荐；按包内 AssetBundle 名建目录）
python scripts/bangdream_extract.py --dir <data目录> --out output
python scripts/bangdream_extract.py --dir <data目录> --out output --what texture
python scripts/bangdream_extract.py --dir <data目录> --list
```

`bangdream_extract.py` 已内置 `FALLBACK_UNITY_VERSION = 2022.3.10f1`，无需手工处理被抹掉的版本号。导出项：`Texture2D` → PNG、`TextAsset` → 原始字节（`.moc` / `.mtn` / `.exp` / `.usm`）、`MonoBehaviour` → JSON、`AudioClip` → WAV。

## 实测导出结果（output/）

15 个样本包全部成功解出：

| 资源 | 内容 |
|---|---|
| `characters/resourceset/res017090` | 卡面 `card_normal.png` 1334×1002、`card_after_training.png`、`trim_*`，以及剧情 JSON |
| `live2d/chara/025_casual` | `rinko.moc`（488 KB 模型）、`rinko.physics`、1024×1024 贴图 |
| `live2d/chara/026_general` | 45 个 `.exp` / `.mtn` 表情与动作文件 |
| `gacha/screen/gacha1958` | 扭蛋界面贴图 12 张 + 9 个 `.usm` 视频分片 |
| `story/bg/background083` | 7 张剧情背景 |
| `musicjacket/musicjacket610` | `jacket.png` 400×400、`thumb.png` 180×180 等 20 张 |
| `starshop/itemset/1238` | 星章商店道具贴图 |

注意 Live2D 用的是 **Cubism 2.1** 格式（`.moc` 魔术字为 `moc`，`.mtn` 文件头为 `# Live2D Animator Motion Data`），用 Cubism 2 Viewer 或 Live2DViewerEX 直接可看，不需要 moc3 工具。

## 已知限制与待办

- 只拷了 15 个样本包，2253 个全量尚未跑完；流程一致，直接对整个 `data` 目录执行即可。
- `AudioClip` 导出路径已写好，但这批样本没有 sound 包，未实测。
- `mf/*.usm.bytes` 是 Criware USM，需要 vgmstream 才能转封装成 MP4。
- `master/cache`、`metamap/AssetBundleMetaMap` 为同风格 protobuf，尚未解析。
- `log/log-*.log` 为二进制混淆格式，尚未解析。

## 版权与许可

- 本仓库的**代码与文档**采用 [MIT License](LICENSE)。
- `output/` 下的游戏美术、音频、模型等资源版权归 **Craft Egg Inc. / Bushiroad / 相关权利人**所有，**不在 MIT 授权范围内**，仅作为技术验证的样本保留在本地。请勿再分发或商用。

本项目仅用于个人设备上已安装游戏的数据研究、备份与本地化尝试，不提供任何绕过正版验证、在线服务或联机功能的手段。
