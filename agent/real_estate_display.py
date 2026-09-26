"""联系方式展示防御（客户 / 业主 / 房源共用的唯一实现，2026-09-25 收口）

问题：`EncryptedString` 在密钥不一致时（换了机器、恢复备份没带密钥文件）会把库里的
密文**原样返回**。任何把它交给上层的出口都会让经纪人听到 `gAAAAAB…`，或者更糟 ——
把它当手机号念给客户。此前客户侧、业主侧、房源侧各写了一套 `_safe_contact` /
`_mask_*_contacts` / `_attach_key_warning`（三份实现、三处容易漏改），实测每次补漏都
只补了一半（结构体漏、写路径漏）。这里收成一份，工具模块只 import。

三种文案刻意保留（不同实体说不同的话）：
- 客户版：只提客户联系方式
- 业主版：只提房东联系方式
- 合并版：按姓名查人一次涉及客户与业主两块
"""
from agent.real_estate_db import KEY_MISMATCH_HINT, looks_like_ciphertext

CUSTOMER_KEY_MISMATCH_WARNING = (
    "客户联系方式读不出来：库里的加密内容用当前密钥解不开"
    "（常见于换了机器、或恢复备份时没带上密钥文件）。先用备份里的密钥文件恢复，"
    "在此之前不要把这条联系方式给客户。")

OWNER_KEY_MISMATCH_WARNING = (
    "房东联系方式读不出来：库里的加密内容用当前密钥解不开"
    "（常见于换了机器、或恢复备份时没带上密钥文件）。先用备份里的密钥文件恢复，"
    "在此之前不要把这条联系方式给客户。")

# 按姓名查人时客户与业主两块联系方式都要提示，用一句合并文案（2026-09-25）
PERSON_KEY_MISMATCH_WARNING = (
    "客户/业主的联系方式读不出来：库里的加密内容用当前密钥解不开"
    "（常见于换了机器、或恢复备份时没带上密钥文件）。先用备份里的密钥文件恢复，"
    "在此之前不要把这条联系方式给客户。")

# 展示防御覆盖的字段：全是 EncryptedString 列
_CONTACT_FIELDS = ("phone", "wechat")


def safe_contact(value):
    """联系方式展示：空 → None；疑似密钥不一致的密文 → 可读提示（绝不把乱码丢给经纪人）"""
    if not value:
        return None
    return KEY_MISMATCH_HINT if looks_like_ciphertext(value) else value


def mask_contacts(row, fields=_CONTACT_FIELDS):
    """把一行里的联系方式做展示防御，返回 (row, 被掩码的字段列表)。

    **读路径与写入路径都要过**：读详情/列客户/组合/反查要过，改生日/改等级这类
    返回整行资料的写工具同样要过 —— 三次漏的都是"只改了文案、结构体没走"。
    """
    masked = []
    for key in fields:
        before = row.get(key)
        if before is None:
            continue
        after = safe_contact(before)
        if after != before:
            masked.append(key)
        row[key] = after
    return row, masked


def attach_key_warning(payload, masked, text=CUSTOMER_KEY_MISMATCH_WARNING):
    """命中密文时给返回体补 warning 与 cipher_fields（让 Coco 如实转述，不静默）

    text 按出口选文案：客户出口（默认）/ 房东出口（OWNER_…）/ 客户+业主出口（PERSON_…）。
    """
    if masked:
        payload["warning_key_mismatch"] = text
        payload["cipher_fields"] = sorted(set(masked))
    return payload
