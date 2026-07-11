from typing import Any

_MAINLINE_DEFS: list[dict[str, Any]] = [
    {
        "name": "机器人 / 物理AI",
        "keywords": ("机器人", "减速器", "工业母机", "机器视觉", "伺服", "传感器", "机械", "自动化", "人形机器人", "智能机器"),
    },
    {
        "name": "PCB / 玻璃基板",
        "keywords": ("PCB", "印制电路", "覆铜板", "玻璃基板", "电子元件", "电子化学品", "封装基板"),
    },
    {
        "name": "CPO / 光模块",
        "keywords": ("CPO", "光模块", "光通信", "通信设备", "光器件", "高速铜缆", "液冷服务器"),
    },
    {
        "name": "创新药",
        "keywords": ("创新药", "化学制药", "生物制品", "生物制药", "医药", "CRO", "CXO", "医疗服务", "合成生物"),
    },
    {
        "name": "电网设备 / 电力",
        "keywords": ("电网", "智能电网", "电力", "电力设备", "发电设备", "电器行业", "风电", "光伏", "特高压", "虚拟电厂", "储能", "绿色电力"),
    },
    {
        "name": "MLCC / 被动元件",
        "keywords": ("MLCC", "被动元件", "电容", "电感", "陶瓷电容", "电子器件"),
    },
    {
        "name": "存储芯片",
        "keywords": ("存储芯片", "存储器", "DRAM", "HBM", "半导体", "芯片", "集成电路"),
    },
    {
        "name": "玻璃玻纤",
        "keywords": ("玻璃玻纤", "玻纤", "玻璃纤维", "建筑材料", "玻璃"),
    },
    {
        "name": "商业航天",
        "keywords": ("商业航天", "卫星", "航天", "军工电子", "国防军工", "军民融合", "航空装备", "飞机制造", "低空经济", "无人机"),
    },
    {
        "name": "能源金属 / 小金属",
        "keywords": ("能源金属", "小金属", "锂", "钴", "镍", "钨", "钼", "稀土", "有色金属", "金属新材料"),
    },
    {
        "name": "贵金属 / 黄金",
        "keywords": ("贵金属", "黄金", "白银", "珠宝首饰", "金银", "黄金概念"),
    },
    {
        "name": "汽车链 / 智能驾驶",
        "keywords": ("汽车", "汽车零部件", "汽车整车", "智能驾驶", "无人驾驶", "汽车电子", "特斯拉"),
    },
    {
        "name": "AI应用 / 算力",
        "keywords": ("人工智能", "AI", "算力", "软件开发", "数据中心", "传媒", "游戏", "数据要素"),
    },
]
_KNOWN_MAINLINE_NAMES = {str(item["name"]) for item in _MAINLINE_DEFS}

_BROAD_STYLE_LABELS = (
    "专精特新", "融资融券", "预盈预增", "机构重仓", "国企改革", "央企改革",
    "深股通", "沪股通", "MSCI", "富时罗素", "标准普尔", "创业板综",
    "中证", "上证", "证金持股", "社保重仓", "QFII", "转债标的",
    "业绩预升", "本月解禁", "券商重仓", "重组概念", "昨日涨停",
    "华为概念", "苹果概念", "小米概念", "恒大概念", "黄河三角",
    "超大盘", "资产注入", "基金重仓", "参股金融",
    "分拆上市", "高校背景", "含H股", "含B股", "业绩预降", "业绩预亏",
    "保险重仓", "金融参股",
)

_SECTOR_TAXONOMY: list[dict[str, Any]] = [
    {
        "category": "半导体链",
        "mainline": "半导体",
        "subline": "集成电路封测",
        "keywords": ("集成电路封测", "封测", "芯片封测"),
    },
    {
        "category": "半导体链",
        "mainline": "半导体",
        "subline": "先进封装",
        "keywords": ("先进封装", "Chiplet", "芯粒", "HBM", "封装基板"),
    },
    {
        "category": "半导体链",
        "mainline": "半导体",
        "subline": "碳化硅",
        "keywords": ("碳化硅", "第三代半导体", "氮化镓", "SiC", "GaN"),
    },
    {
        "category": "半导体链",
        "mainline": "半导体",
        "subline": "存储芯片",
        "keywords": ("存储芯片", "存储器", "DRAM", "NAND"),
    },
    {
        "category": "半导体链",
        "mainline": "半导体",
        "subline": "半导体",
        "keywords": ("半导体", "芯片", "集成电路", "光刻机", "光刻胶", "EDA", "晶圆", "电子化学品", "华为海思"),
    },
    {
        "category": "AI算力链",
        "mainline": "CPO / 光模块",
        "subline": "CPO概念",
        "keywords": ("CPO", "共封装光学"),
    },
    {
        "category": "AI算力链",
        "mainline": "CPO / 光模块",
        "subline": "光模块",
        "keywords": ("光模块", "光通信", "光器件", "通信设备"),
    },
    {
        "category": "AI算力链",
        "mainline": "AI应用 / 算力",
        "subline": "算力",
        "keywords": ("算力", "液冷服务器", "数据中心", "服务器", "人工智能", "AI"),
    },
    {
        "category": "消费电子",
        "mainline": "消费电子",
        "subline": "光学光电子",
        "keywords": ("光学光电子", "消费电子", "电子元件", "面板", "OLED", "MiniLED", "MicroLED", "触摸屏"),
    },
    {
        "category": "有色金属链",
        "mainline": "有色金属",
        "subline": "贵金属 / 黄金",
        "keywords": ("贵金属", "黄金", "白银", "珠宝首饰", "金银"),
    },
    {
        "category": "有色金属链",
        "mainline": "有色金属",
        "subline": "能源金属",
        "keywords": ("能源金属", "锂", "钴", "镍"),
    },
    {
        "category": "有色金属链",
        "mainline": "有色金属",
        "subline": "小金属",
        "keywords": ("小金属", "稀土", "钨", "钼", "铟", "锑", "金属新材料"),
    },
    {
        "category": "有色金属链",
        "mainline": "有色金属",
        "subline": "有色金属",
        "keywords": ("有色金属", "铜", "铝", "铅", "锌"),
    },
    {
        "category": "商业航天",
        "mainline": "商业航天",
        "subline": "商业航天",
        "keywords": ("商业航天", "卫星", "航天", "军工电子", "国防军工", "航空装备", "低空经济", "无人机"),
    },
    {
        "category": "机器人",
        "mainline": "机器人 / 物理AI",
        "subline": "机器人",
        "keywords": ("机器人", "智能机器", "人形机器人", "减速器", "伺服", "机器视觉", "工业母机", "自动化", "3D打印"),
    },
    {
        "category": "汽车链",
        "mainline": "汽车链 / 智能驾驶",
        "subline": "新能源汽车",
        "keywords": ("汽车", "新能源汽车", "汽车零部件", "汽车整车", "智能驾驶", "无人驾驶", "特斯拉"),
    },
    {
        "category": "医药",
        "mainline": "创新药",
        "subline": "创新药",
        "keywords": ("创新药", "化学制药", "生物制品", "CRO", "CXO", "医药", "医疗服务", "合成生物"),
    },
    {
        "category": "金融地产",
        "mainline": "金融地产",
        "subline": "房地产",
        "keywords": ("房地产", "物业管理", "租售同权"),
    },
    {
        "category": "金融地产",
        "mainline": "金融地产",
        "subline": "金融",
        "keywords": ("证券", "银行", "保险", "多元金融", "互联金融"),
    },
]

def _classify_sector_taxonomy(raw: dict[str, Any]) -> dict[str, str]:
    raw_name = str(raw.get("name") or "未知板块").strip() or "未知板块"
    text = raw_name.upper()
    for item in _SECTOR_TAXONOMY:
        for keyword in item["keywords"]:
            if str(keyword).upper() in text:
                return {
                    "display_name": raw_name,
                    "raw_name": raw_name,
                    "mainline": str(item["mainline"]),
                    "subline": str(item["subline"]),
                    "category": str(item["category"]),
                }
    return {
        "display_name": raw_name,
        "raw_name": raw_name,
        "mainline": raw_name,
        "subline": raw_name,
        "category": "其他",
    }
