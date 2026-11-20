# permissions.py
from __future__ import annotations

from dataclasses import dataclass
import discord

from config import Settings


@dataclass(frozen=True, slots=True)
class PermissionDiagnosticReport:
    channel_name: str
    channel_id: int
    bot_top_role_name: str
    bot_top_role_position: int
    total_guild_roles: int
    permissions: dict[str, bool]

    @property
    def all_critical_granted(self) -> bool:
        critical_keys = (
            "view_channel",
            "send_messages",
            "embed_links",
            "read_message_history",
            "manage_messages",
            "move_members",
        )
        return all(self.permissions.get(k, False) for k in critical_keys)


def is_whitelisted(user_id: int, settings: Settings) -> bool:
    return user_id in settings.whitelist_ids


def is_vip(user: discord.abc.User | discord.Member, settings: Settings) -> bool:
    if user.id in settings.vip_user_ids or user.id in settings.whitelist_ids:
        return True

    if isinstance(user, discord.Member):
        if user.guild.owner_id == user.id:
            return True
        if user.guild_permissions.administrator or user.guild_permissions.manage_guild:
            return True
        member_role_ids = {role.id for role in user.roles}
        if member_role_ids & settings.vip_role_ids:
            return True

    return False


def build_permission_diagnostics(
    guild: discord.Guild,
    channel: discord.abc.GuildChannel,
) -> PermissionDiagnosticReport:
    me: discord.Member = guild.me
    perms: discord.Permissions = channel.permissions_for(me)

    tracked_permissions: dict[str, bool] = {
        "view_channel": perms.view_channel,
        "send_messages": perms.send_messages,
        "embed_links": perms.embed_links,
        "attach_files": perms.attach_files,
        "read_message_history": perms.read_message_history,
        "manage_messages": perms.manage_messages,
        "move_members": guild.me.guild_permissions.move_members,
        "mute_members": guild.me.guild_permissions.mute_members,
    }

    return PermissionDiagnosticReport(
        channel_name=getattr(channel, "name", str(channel.id)),
        channel_id=channel.id,
        bot_top_role_name=me.top_role.name,
        bot_top_role_position=me.top_role.position,
        total_guild_roles=len(guild.roles),
        permissions=tracked_permissions,
    )


def translate_forbidden_error(
    error: discord.Forbidden,
    guild: discord.Guild | None = None,
    channel: discord.abc.GuildChannel | None = None,
    target_member: discord.Member | None = None,
    action_description: str = "執行此操作",
) -> str:
    reasons: list[str] = []

    if guild is not None and channel is not None:
        me = guild.me
        perms = channel.permissions_for(me)
        if not perms.view_channel:
            reasons.append("缺少「檢視頻道 (`view_channel`)」權限")
        if not perms.send_messages:
            reasons.append("缺少「發送訊息 (`send_messages`)」權限")
        if not perms.read_message_history:
            reasons.append("缺少「讀取訊息歷史 (`read_message_history`)」權限")
        if not perms.manage_messages:
            reasons.append("缺少「管理訊息 (`manage_messages`)」權限（刪除或批次清除訊息必備）")
        if not me.guild_permissions.move_members:
            reasons.append("缺少「移動成員 (`move_members`)」權限（語音頻道調動必備）")

    if guild is not None and target_member is not None:
        me = guild.me
        if target_member.id == guild.owner_id:
            reasons.append("目標成員為伺服器擁有者，機器人無法對其進行管理操作")
        elif me.top_role <= target_member.top_role:
            reasons.append(
                f"身分組階層不足：機器人最高身分組為「{me.top_role.name}」(階層 {me.top_role.position})，"
                f"低於或等於目標成員的最高身分組「{target_member.top_role.name}」(階層 {target_member.top_role.position})"
            )

    if not reasons:
        reasons.append(f"Discord API 回傳 403 Forbidden（原始訊息：{error.text or '無詳細說明'}）")

    bullet_list = "\n".join(f"• {r}" for r in reasons)
    return (
        f"⚠️ **權限不足，無法{action_description}**\n"
        f"診斷結果如下：\n{bullet_list}\n"
        f"💡 請管理員執行 `/checkperms` 檢視完整權限狀態，或調整機器人身分組順序。"
    )