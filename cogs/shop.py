"""포인트 상점.

`/상점` 을 치면 **역할상점**과 **기타상점** 두 갈래가 나온다.

  · 역할상점 — 색상 역할. 닉네임 색이 바뀐다.
  · 기타상점 — 프로필 카드 테마와 문구.

모든 아이템은 **30일 기간제**다. 영구로 팔면 두어 달 만에 다들 사고 끝나서
포인트를 쓸 곳이 없어진다. 만료되면 시간당 도는 정리 작업이 되돌린다.
"""
from __future__ import annotations

import datetime as dt
import logging

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import (
    CARD_THEMES,
    Channels,
    Colors,
    Economy,
    GUILD_ID,
    SHOP_THEME_KEYS,
    Shop,
)
from core.checks import staff_only
from utils.logs import base_embed, send_log, user_field

log = logging.getLogger("mainbot.shop")

# 상점이 만든 색상 역할 ID 를 담아 두는 설정 키
COLOR_ROLES_SETTING = "shop_color_roles"

KIND_THEME = "theme"
KIND_SLOGAN = "slogan"
KIND_COLOR_ROLE = "color_role"

KIND_LABELS = {
    KIND_THEME: "프로필 테마",
    KIND_SLOGAN: "프로필 문구",
    KIND_COLOR_ROLE: "색상 역할",
}


def fmt_points(value: int) -> str:
    return f"{value:,}{Economy.UNIT}"


def fmt_expiry(raw: str) -> str:
    """만료 시각을 디스코드 상대 시간으로."""
    try:
        moment = dt.datetime.fromisoformat(raw)
    except ValueError:
        return raw
    return discord.utils.format_dt(moment, "R")


# --------------------------------------------------------------- 구매 처리


class ShopError(Exception):
    """사용자에게 그대로 보여줄 구매 실패 사유."""


class ShopCog(commands.Cog, name="Shop"):
    """상점과 구매 아이템 관리."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.expire_items.start()

    async def cog_unload(self) -> None:
        self.expire_items.cancel()

    # ----------------------------------------------------------- 색상 역할

    async def color_role_map(self) -> dict[str, int]:
        """테마 키 → 색상 역할 ID. `/색상역할생성` 으로 만들어진다."""
        return {k: int(v) for k, v in (await self.bot.db.get_json_setting(
            COLOR_ROLES_SETTING
        ) or {}).items()}

    @app_commands.command(
        name="색상역할생성",
        description="[관리자] 상점에서 팔 색상 역할을 자동으로 만듭니다.",
    )
    @staff_only()
    async def create_color_roles(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            return
        if not guild.me.guild_permissions.manage_roles:
            await interaction.response.send_message(
                "봇에게 **역할 관리** 권한이 없습니다.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        existing = await self.color_role_map()
        created, kept = [], []
        for key in SHOP_THEME_KEYS:
            spec = CARD_THEMES[key]
            role_id = existing.get(key)
            if role_id and guild.get_role(role_id) is not None:
                kept.append(spec.name)
                continue
            try:
                role = await guild.create_role(
                    name=f"🎨 {spec.name}",
                    colour=discord.Colour.from_rgb(*spec.accent),
                    reason=f"상점 색상 역할 생성 — {interaction.user}",
                )
            except discord.Forbidden:
                await interaction.followup.send(
                    "역할을 만들 권한이 없습니다.", ephemeral=True
                )
                return
            except discord.HTTPException as exc:
                await interaction.followup.send(
                    f"역할 생성 실패: `{exc}`", ephemeral=True
                )
                return
            existing[key] = role.id
            created.append(spec.name)

        await self.bot.db.set_json_setting(COLOR_ROLES_SETTING, existing)

        embed = base_embed(
            "🎨 색상 역할 준비 완료",
            Colors.SUCCESS,
            description=(
                "역할상점에서 팔 색상 역할을 정리했습니다.\n"
                "**봇 역할을 이 역할들보다 위로 올려 주세요.** 아니면 지급되지 않습니다."
            ),
        )
        embed.add_field(
            name=f"새로 만듦 ({len(created)})", value=", ".join(created) or "없음", inline=False
        )
        embed.add_field(
            name=f"이미 있음 ({len(kept)})", value=", ".join(kept) or "없음", inline=False
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    # -------------------------------------------------------------- 구매

    async def purchase(
        self,
        member: discord.Member,
        kind: str,
        item_key: str,
        price: int,
        *,
        value: str | None = None,
    ) -> str:
        """포인트를 차감하고 아이템을 적용한다. 만료 시각을 돌려준다.

        실패하면 ShopError 를 던진다. 포인트는 적용에 성공한 뒤에만 빠진다.
        """
        balance = await self.bot.db.get_points(member.id)
        if balance < price:
            raise ShopError(
                f"포인트가 부족합니다. **{fmt_points(price)}** 가 필요한데 "
                f"지금 {fmt_points(balance)} 가지고 계십니다."
            )

        # 역할은 실제로 붙는지 먼저 확인한 뒤 포인트를 뺀다
        if kind == KIND_COLOR_ROLE:
            await self._apply_color_role(member, item_key)

        await self.bot.db.add_points(
            member.id, -price, f"상점 구매 — {KIND_LABELS.get(kind, kind)}"
        )
        expires = await self.bot.db.add_purchase(
            member.id, kind, item_key, price, Shop.DURATION_DAYS, value
        )

        await self._log_purchase(member, kind, item_key, price, value, expires)
        return expires

    async def _apply_color_role(self, member: discord.Member, key: str) -> None:
        """색상 역할을 지급하고, 이전에 산 다른 색은 회수한다."""
        roles = await self.color_role_map()
        role_id = roles.get(key)
        role = member.guild.get_role(role_id) if role_id else None
        if role is None:
            raise ShopError(
                "색상 역할이 아직 준비되지 않았습니다. "
                "관리자에게 `/색상역할생성` 실행을 요청해 주세요."
            )
        if role >= member.guild.me.top_role:
            raise ShopError(
                f"{role.mention} 역할이 봇보다 높아 지급할 수 없습니다. "
                "관리자에게 봇 역할 순서를 올려 달라고 요청해 주세요."
            )

        others = [
            other
            for other_key, other_id in roles.items()
            if other_key != key
            and (other := member.guild.get_role(other_id)) is not None
            and other in member.roles
        ]
        try:
            if role not in member.roles:
                await member.add_roles(role, reason="상점 색상 역할 구매")
            if others:
                await member.remove_roles(*others, reason="상점 색상 역할 변경")
        except discord.Forbidden as exc:
            raise ShopError("봇에게 역할을 줄 권한이 없습니다.") from exc
        except discord.HTTPException as exc:
            raise ShopError(f"역할 지급에 실패했습니다: `{exc}`") from exc

    async def _log_purchase(
        self,
        member: discord.Member,
        kind: str,
        item_key: str,
        price: int,
        value: str | None,
        expires: str,
    ) -> None:
        label = KIND_LABELS.get(kind, kind)
        name = CARD_THEMES[item_key].name if item_key in CARD_THEMES else item_key
        embed = base_embed("🛒 상점 구매", Colors.GOLD)
        embed.set_author(name=str(member), icon_url=member.display_avatar.url)
        embed.add_field(name="구매자", value=user_field(member), inline=True)
        embed.add_field(name="상품", value=f"{label} · {name}", inline=True)
        embed.add_field(name="가격", value=f"-{fmt_points(price)}", inline=True)
        if value:
            embed.add_field(name="내용", value=f"`{value}`", inline=False)
        embed.add_field(name="만료", value=fmt_expiry(expires), inline=True)
        await send_log(self.bot, Channels.POINT_LOG, embed)

    # ----------------------------------------------------------- 만료 정리

    @tasks.loop(hours=1)
    async def expire_items(self) -> None:
        """기간이 끝난 색상 역할을 회수한다.

        테마와 문구는 조회할 때 만료를 걸러 내므로 따로 되돌릴 것이 없다.
        """
        for guild in self.bot.guilds:
            if GUILD_ID is not None and guild.id != GUILD_ID:
                continue
            roles = await self.color_role_map()
            if not roles:
                continue

            active = {
                (int(row["user_id"]), str(row["item_key"]))
                for row in await self.bot.db.active_by_kind(KIND_COLOR_ROLE)
            }
            removed = 0
            for key, role_id in roles.items():
                role = guild.get_role(role_id)
                if role is None:
                    continue
                for member in list(role.members):
                    if (member.id, key) in active:
                        continue
                    try:
                        await member.remove_roles(role, reason="상점 아이템 기간 만료")
                        removed += 1
                    except discord.HTTPException:
                        pass
            if removed:
                log.info("[%s] 만료된 색상 역할 %d개를 회수했습니다.", guild.name, removed)

    @expire_items.before_loop
    async def before_expire_items(self) -> None:
        await self.bot.wait_until_ready()

    # -------------------------------------------------------------- 명령어

    @app_commands.command(name="상점", description="포인트로 살 수 있는 것들을 봅니다.")
    async def shop(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "서버 안에서만 사용할 수 있습니다.", ephemeral=True
            )
            return
        balance = await self.bot.db.get_points(interaction.user.id)
        await interaction.response.send_message(
            embed=home_embed(balance),
            view=ShopView(self, interaction.user),
            ephemeral=True,
        )

    @app_commands.command(name="내아이템", description="구매한 아이템과 남은 기간을 봅니다.")
    async def my_items(self, interaction: discord.Interaction) -> None:
        rows = await self.bot.db.active_purchases(interaction.user.id)
        balance = await self.bot.db.get_points(interaction.user.id)

        embed = base_embed(
            "🎒 내 아이템",
            Colors.GOLD,
            description=f"보유 포인트 **{fmt_points(balance)}**",
        )
        if not rows:
            embed.add_field(
                name="보유 중인 아이템",
                value="없습니다. `/상점` 에서 둘러보세요.",
                inline=False,
            )
        else:
            for row in rows:
                key = str(row["item_key"])
                name = CARD_THEMES[key].name if key in CARD_THEMES else key
                detail = f"`{row['value']}`" if row["value"] else name
                embed.add_field(
                    name=KIND_LABELS.get(str(row["kind"]), str(row["kind"])),
                    value=f"{detail}\n만료 {fmt_expiry(str(row['expires_at']))}",
                    inline=True,
                )
        await interaction.response.send_message(embed=embed, ephemeral=True)


# ------------------------------------------------------------------- UI


def home_embed(balance: int) -> discord.Embed:
    embed = base_embed(
        "🛒 포인트 상점",
        Colors.GOLD,
        description=(
            f"보유 포인트 **{fmt_points(balance)}**\n"
            f"모든 아이템은 **{Shop.DURATION_DAYS}일**간 유지됩니다."
        ),
    )
    embed.add_field(
        name="🎨 역할상점",
        value=f"닉네임 색이 바뀌는 색상 역할 · {fmt_points(Shop.COLOR_ROLE_PRICE)}",
        inline=False,
    )
    embed.add_field(
        name="🪪 기타상점",
        value=(
            f"프로필 카드 테마 · {fmt_points(Shop.THEME_PRICE)}\n"
            f"프로필 카드 문구 · {fmt_points(Shop.SLOGAN_PRICE)}"
        ),
        inline=False,
    )
    embed.set_footer(text="롤 같이 하자 · 아래에서 분류를 골라 주세요")
    return embed


class SloganModal(discord.ui.Modal, title="프로필 문구 바꾸기"):
    """카드 좌상단에 들어갈 문구를 입력받는다."""

    text = discord.ui.TextInput(
        label="문구",
        placeholder="협곡의 지배자",
        max_length=Shop.SLOGAN_MAX_LENGTH,
        min_length=1,
    )

    def __init__(self, cog: ShopCog, member: discord.Member) -> None:
        super().__init__()
        self.cog = cog
        self.member = member

    async def on_submit(self, interaction: discord.Interaction) -> None:
        value = " ".join(str(self.text.value).split())
        if not value:
            await interaction.response.send_message(
                "문구가 비어 있습니다.", ephemeral=True
            )
            return
        try:
            expires = await self.cog.purchase(
                self.member, KIND_SLOGAN, "slogan", Shop.SLOGAN_PRICE, value=value
            )
        except ShopError as exc:
            await interaction.response.send_message(f"❌ {exc}", ephemeral=True)
            return

        balance = await self.cog.bot.db.get_points(self.member.id)
        embed = base_embed(
            "✅ 구매 완료",
            Colors.SUCCESS,
            description=(
                f"프로필 문구가 **`{value}`** 로 바뀌었습니다.\n"
                f"만료 {fmt_expiry(expires)} · 남은 포인트 {fmt_points(balance)}"
            ),
        )
        embed.set_footer(text="/프로필 로 확인해 보세요")
        await interaction.response.send_message(embed=embed, ephemeral=True)


class ShopView(discord.ui.View):
    """분류 선택 → 아이템 선택으로 이어지는 상점 화면."""

    def __init__(self, cog: ShopCog, member: discord.Member) -> None:
        super().__init__(timeout=180)
        self.cog = cog
        self.member = member
        self.add_item(CategorySelect())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.member.id:
            await interaction.response.send_message(
                "본인이 연 상점에서만 구매할 수 있습니다. `/상점` 을 직접 써 주세요.",
                ephemeral=True,
            )
            return False
        return True

    async def show_category(
        self, interaction: discord.Interaction, category: str
    ) -> None:
        balance = await self.cog.bot.db.get_points(self.member.id)
        self.clear_items()
        self.add_item(CategorySelect(default=category))

        if category == "role":
            roles = await self.cog.color_role_map()
            embed = base_embed(
                "🎨 역할상점",
                Colors.GOLD,
                description=(
                    f"닉네임 색이 바뀌는 색상 역할입니다. "
                    f"**{fmt_points(Shop.COLOR_ROLE_PRICE)} / {Shop.DURATION_DAYS}일**\n"
                    f"보유 포인트 **{fmt_points(balance)}**"
                ),
            )
            if not roles:
                embed.color = Colors.DANGER
                embed.add_field(
                    name="아직 준비되지 않았습니다",
                    value="관리자가 `/색상역할생성` 을 실행하면 열립니다.",
                    inline=False,
                )
            else:
                embed.add_field(
                    name="색상",
                    value="\n".join(
                        f"<@&{rid}> — {CARD_THEMES[k].name}"
                        for k, rid in roles.items()
                        if k in CARD_THEMES
                    ),
                    inline=False,
                )
                self.add_item(ColorRoleSelect(roles))
        else:
            embed = base_embed(
                "🪪 기타상점",
                Colors.TEAL,
                description=(
                    f"`/프로필` 카드를 꾸밉니다. 모두 **{Shop.DURATION_DAYS}일** 유지.\n"
                    f"보유 포인트 **{fmt_points(balance)}**"
                ),
            )
            embed.add_field(
                name=f"카드 테마 · {fmt_points(Shop.THEME_PRICE)}",
                value=(
                    "테두리 · 게이지 · 강조색이 통째로 바뀝니다.\n"
                    + " · ".join(CARD_THEMES[k].name for k in SHOP_THEME_KEYS)
                ),
                inline=False,
            )
            embed.add_field(
                name=f"카드 문구 · {fmt_points(Shop.SLOGAN_PRICE)}",
                value=(
                    "카드 좌상단의 「롤 같이 하자」를 원하는 문구로 바꿉니다. "
                    f"(최대 {Shop.SLOGAN_MAX_LENGTH}자)"
                ),
                inline=False,
            )
            self.add_item(ThemeSelect())
            self.add_item(SloganButton())

        await interaction.response.edit_message(embed=embed, view=self)


class CategorySelect(discord.ui.Select):
    def __init__(self, default: str | None = None) -> None:
        super().__init__(
            placeholder="분류를 골라 주세요",
            options=[
                discord.SelectOption(
                    label="역할상점", value="role", emoji="🎨",
                    description="닉네임 색이 바뀌는 색상 역할",
                    default=default == "role",
                ),
                discord.SelectOption(
                    label="기타상점", value="misc", emoji="🪪",
                    description="프로필 카드 테마 · 문구",
                    default=default == "misc",
                ),
            ],
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.view.show_category(interaction, self.values[0])  # type: ignore[attr-defined]


class ThemeSelect(discord.ui.Select):
    def __init__(self) -> None:
        super().__init__(
            placeholder="카드 테마 고르기",
            options=[
                discord.SelectOption(
                    label=CARD_THEMES[key].name,
                    value=key,
                    description=f"{Shop.THEME_PRICE:,}P · {Shop.DURATION_DAYS}일",
                )
                for key in SHOP_THEME_KEYS
            ],
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view: ShopView = self.view  # type: ignore[assignment]
        key = self.values[0]
        try:
            expires = await view.cog.purchase(
                view.member, KIND_THEME, key, Shop.THEME_PRICE
            )
        except ShopError as exc:
            await interaction.response.send_message(f"❌ {exc}", ephemeral=True)
            return

        balance = await view.cog.bot.db.get_points(view.member.id)
        embed = base_embed(
            "✅ 구매 완료",
            Colors.SUCCESS,
            description=(
                f"프로필 카드 테마가 **{CARD_THEMES[key].name}** 으로 바뀌었습니다.\n"
                f"만료 {fmt_expiry(expires)} · 남은 포인트 {fmt_points(balance)}"
            ),
        )
        embed.set_footer(text="/프로필 로 확인해 보세요")
        await interaction.response.send_message(embed=embed, ephemeral=True)


class ColorRoleSelect(discord.ui.Select):
    def __init__(self, roles: dict[str, int]) -> None:
        super().__init__(
            placeholder="색상 고르기",
            options=[
                discord.SelectOption(
                    label=CARD_THEMES[key].name,
                    value=key,
                    description=f"{Shop.COLOR_ROLE_PRICE:,}P · {Shop.DURATION_DAYS}일",
                )
                for key in roles
                if key in CARD_THEMES
            ][:25],
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view: ShopView = self.view  # type: ignore[assignment]
        key = self.values[0]
        try:
            expires = await view.cog.purchase(
                view.member, KIND_COLOR_ROLE, key, Shop.COLOR_ROLE_PRICE
            )
        except ShopError as exc:
            await interaction.response.send_message(f"❌ {exc}", ephemeral=True)
            return

        balance = await view.cog.bot.db.get_points(view.member.id)
        await interaction.response.send_message(
            embed=base_embed(
                "✅ 구매 완료",
                Colors.SUCCESS,
                description=(
                    f"**{CARD_THEMES[key].name}** 색상 역할을 받았습니다.\n"
                    f"만료 {fmt_expiry(expires)} · 남은 포인트 {fmt_points(balance)}"
                ),
            ),
            ephemeral=True,
        )


class SloganButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="카드 문구 바꾸기", emoji="✏️",
            style=discord.ButtonStyle.primary, row=2,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view: ShopView = self.view  # type: ignore[assignment]
        await interaction.response.send_modal(SloganModal(view.cog, view.member))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ShopCog(bot))
