from typing import Any

_MAINLINE_DEFS: list[dict[str, Any]] = [
    {
        "name": "机器人 / 物理AI",
        # These are provider board aliases, not free-text keywords.  Theme
        # classification must never be driven by the name of a temporary
        # leading stock (for example a stock containing “机械” must not turn an
        # unrelated concept board into the robot mainline).
        "keywords": ("机器人", "机器人概念", "虚拟机器人", "人形机器人", "智能机器人", "智能机器", "减速器", "减速器概念", "机器人执行器", "机器视觉", "伺服系统", "工业母机"),
    },
    {
        "name": "PCB / 玻璃基板",
        "keywords": ("PCB", "PCB概念", "印制电路板", "覆铜板", "玻璃基板", "封装基板"),
    },
    {
        "name": "CPO / 光模块",
        "keywords": ("CPO", "CPO概念", "光模块", "光通信模块", "光器件"),
    },
    {
        "name": "创新药",
        "keywords": ("创新药", "创新药概念", "CRO", "CRO概念", "CXO", "CXO概念"),
    },
    {
        "name": "电网设备 / 电力",
        "keywords": ("电力", "绿色电力", "智能电网", "电网设备", "特高压", "虚拟电厂", "超超临界发电"),
    },
    {
        "name": "MLCC / 被动元件",
        "keywords": ("MLCC", "被动元件", "被动元件概念", "陶瓷电容"),
    },
    {
        "name": "存储芯片",
        "keywords": ("存储芯片", "存储器", "DRAM", "NAND", "HBM"),
    },
    {
        "name": "半导体 / 芯片",
        "keywords": ("半导体", "半导体概念", "国产芯片", "芯片概念", "集成电路", "第三代半导体", "第四代半导体", "汽车芯片", "AI芯片"),
    },
    {
        "name": "玻璃玻纤",
        "keywords": ("玻璃玻纤", "玻纤", "玻璃纤维"),
    },
    {
        "name": "商业航天",
        "keywords": ("商业航天", "商业航天概念", "卫星互联网", "卫星导航"),
    },
    {
        "name": "能源金属 / 小金属",
        "keywords": ("能源金属", "小金属", "小金属概念", "锂矿", "锂矿概念", "稀土永磁"),
    },
    {
        "name": "贵金属 / 黄金",
        "keywords": ("贵金属", "黄金", "白银", "珠宝首饰", "金银", "黄金概念"),
    },
    {
        "name": "汽车链 / 智能驾驶",
        "keywords": ("智能驾驶", "无人驾驶", "汽车电子", "汽车整车", "汽车零部件"),
    },
    {
        "name": "AI应用 / 算力",
        "keywords": ("人工智能", "人工智能概念", "AI应用", "AI语料", "算力", "算力概念", "数据中心", "数据要素"),
    },
]
_KNOWN_MAINLINE_NAMES = {str(item["name"]) for item in _MAINLINE_DEFS}

_BROAD_STYLE_LABELS = (
    "融资融券", "预盈预增", "机构重仓",
    "深股通", "沪股通", "MSCI", "富时罗素", "标准普尔", "创业板综",
    "中证", "上证", "证金持股", "社保重仓", "QFII", "转债标的",
    "业绩预升", "本月解禁", "券商重仓", "昨日涨停",
    "超大盘", "基金重仓", "高校背景", "含H股", "含B股", "业绩预降", "业绩预亏",
    "保险重仓", "金融参股",
    "权重股", "茅指数", "大盘价值", "上证50", "沪深300", "中证500",
    "昨日首板", "昨日连板", "昨日涨停表现", "近期新高", "百日新高", "GDR",
    # Pure index/high-history/passive-holding baskets are not investable
    # narratives. Do not permanently filter policy, restructuring or major
    # supply-chain concepts: when their real flow, breadth and limit-up
    # evidence is strong they may legitimately be a market mainline.
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
