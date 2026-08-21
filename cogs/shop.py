"""포인트 상점.

`/상점` 을 치면 **역할상점 · 챔피언상점 · 기타상점** 세 갈래가 나온다.

  · 역할상점 — 색상 역할과 LCK 응원 역할. 닉네임 색이 바뀐다.
  · 챔피언상점 — 챔피언 60종. 라인을 고르면 그 라인 챔피언만 보인다.
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
    shop_duration,
    CARD_THEMES,
    CHAMPIONS,
    Channels,
    Colors,
    Economy,
    GRADIENT_ROLE_FEATURE,
    GUILD_ID,
    LANE_SHORT,
    LCK_TEAMS,
    SHOP_THEME_KEYS,
    Shop,
    champions_in_lane,
)
from core.checks import staff_only
from utils.logs import base_embed, send_log, user_field

log = logging.getLogger("mainbot.shop")

# 상점이 만든 역할 ID 를 담아 두는 설정 키
COLOR_ROLES_SETTING = "shop_color_roles"
TEAM_ROLES_SETTING = "shop_team_roles"
CHAMPION_ROLES_SETTING = "shop_champion_roles"

KIND_THEME = "theme"
KIND_SLOGAN = "slogan"
KIND_COLOR_ROLE = "color_role"
KIND_TEAM_ROLE = "team_role"
KIND_CHAMPION_ROLE = "champion_role"

KIND_LABELS = {
    KIND_THEME: "프로필 테마",
    KIND_SLOGAN: "프로필 문구",
    KIND_COLOR_ROLE: "색상 역할",
    KIND_TEAM_ROLE: "LCK 응원 역할",
    KIND_CHAMPION_ROLE: "챔피언 역할",
}

# 역할을 파는 갈래들. 각각 같은 갈래 안에서는 하나만 가질 수 있다.
ROLE_KINDS = {
    KIND_COLOR_ROLE: COLOR_ROLES_SETTING,
    KIND_TEAM_ROLE: TEAM_ROLES_SETTING,
    KIND_CHAMPION_ROLE: CHAMPION_ROLES_SETTING,
}

# 갈래별로 역할을 준비하는 관리자 명령어 (아직 안 만들었을 때 안내에 쓴다)
ROLE_SETUP_COMMANDS = {
    KIND_COLOR_ROLE: "`/색상역할생성`",
    KIND_TEAM_ROLE: "`/응원역할생성`",
    KIND_CHAMPION_ROLE: "`/챔피언역할생성`",
}

# 서버 하나가 가질 수 있는 역할 수 한계. 60종을 한 번에 만들기 전에 확인한다.
GUILD_ROLE_LIMIT = 250

GRADIENT_MARK = "✨"


def item_name(key: str) -> str:
    """상품 키를 사람이 읽을 이름으로."""
    if key in CARD_THEMES:
        return CARD_THEMES[key].name
    if key in LCK_TEAMS:
        return LCK_TEAMS[key].name
    if key in CHAMPIONS:
        return CHAMPIONS[key].name
    return key


def champion_price(key: str) -> int:
    """챔피언 역할 값. 그라데이션이 붙은 20종은 따로 비싸다."""
    spec = CHAMPIONS.get(key)
    if spec is not None and spec.gradient:
        return Shop.CHAMPION_GRADIENT_PRICE
    return Shop.CHAMPION_ROLE_PRICE


def fmt_points(value: int) -> str:
    return f"{value:,}{Economy.UNIT}"


def join_names(names: list[str], *, limit: int = 900) -> str:
    """임베드 필드(1024자)를 넘기지 않도록 이름 목록을 잘라 잇는다."""
    if not names:
        return "없음"
    out: list[str] = []
    used = 0
    for name in names:
        if used + len(name) + 2 > limit:
            return " · ".join(out) + f" 외 {len(names) - len(out)}종"
        out.append(name)
        used += len(name) + 2
    return " · ".join(out)


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

    async def role_map(self, kind: str) -> dict[str, int]:
        """상품 키 → 역할 ID. `ROLE_SETUP_COMMANDS` 의 명령어들이 채운다."""
        setting = ROLE_KINDS[kind]
        raw = await self.bot.db.get_json_setting(setting) or {}
        return {k: int(v) for k, v in raw.items()}

    async def _ensure_roles(
        self,
        interaction: discord.Interaction,
        kind: str,
        specs: dict[str, object],
        *,
        prefix: str = "",
    ) -> None:
        """상품마다 역할을 하나씩 준비한다.

        같은 이름의 역할이 이미 서버에 있으면 새로 만들지 않고 그것을 쓴다.
        (수동으로 만들어 둔 팀 역할을 중복 생성하지 않기 위해서다.)

        이미 등록해 둔 역할의 이름이 설정과 달라졌으면 **역할 이름을 고친다.**
        새로 만들어 버리면 같은 상품의 역할이 두 개가 되기 때문이다.
        """
        guild = interaction.guild
        if guild is None:
            return
        if not guild.me.guild_permissions.manage_roles:
            await interaction.response.send_message(
                "봇에게 **역할 관리** 권한이 없습니다.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        existing = await self.role_map(kind)
        by_name = {role.name: role for role in guild.roles}
        created, reused, kept, renamed, stuck = [], [], [], [], []

        # 그라데이션은 서버 기능이 켜져 있을 때만 쓸 수 있다
        gradient_ok = GRADIENT_ROLE_FEATURE in guild.features
        gradient_used = False

        # 새로 만들어야 할 개수가 서버 역할 한계를 넘으면 아예 시작하지 않는다.
        # (절반만 만들어 두고 실패하면 정리가 성가시다)
        missing = sum(
            1
            for key, spec in specs.items()
            if not (
                (rid := existing.get(key)) and guild.get_role(rid) is not None
            )
            and f"{prefix}{spec.name}" not in by_name  # type: ignore[attr-defined]
        )
        if len(guild.roles) + missing > GUILD_ROLE_LIMIT:
            await interaction.followup.send(
                f"역할을 **{missing}개** 더 만들어야 하는데, 서버 역할이 이미 "
                f"{len(guild.roles)}개라 한계({GUILD_ROLE_LIMIT}개)를 넘습니다.\n"
                "쓰지 않는 역할을 먼저 정리해 주세요.",
                ephemeral=True,
            )
            return

        for key, spec in specs.items():
            label = f"{prefix}{spec.name}"  # type: ignore[attr-defined]

            role_id = existing.get(key)
            known = guild.get_role(role_id) if role_id else None
            if known is not None:
                # 이미 등록된 역할. 설정에서 이름만 바뀌었으면 역할 이름도 맞춰 준다.
                # (새로 만들면 같은 상품의 역할이 두 개가 되어 버린다)
                if known.name != label:
                    before = known.name
                    try:
                        await known.edit(
                            name=label, reason=f"상점 역할 이름 변경 — {interaction.user}"
                        )
                        renamed.append(f"{before} → {label}")
                    except discord.HTTPException:
                        # 봇보다 높은 역할은 고칠 수 없다
                        stuck.append(before)
                else:
                    kept.append(spec.name)  # type: ignore[attr-defined]
                continue

            found = by_name.get(label)
            if found is not None:
                existing[key] = found.id
                reused.append(spec.name)  # type: ignore[attr-defined]
                continue

            second = getattr(spec, "secondary", None)
            extra = {}
            if gradient_ok and second is not None:
                extra["secondary_colour"] = discord.Colour.from_rgb(*second)
                gradient_used = True

            try:
                role = await guild.create_role(
                    name=label,
                    colour=discord.Colour.from_rgb(*spec.color),  # type: ignore[attr-defined]
                    reason=f"상점 역할 생성 — {interaction.user}",
                    **extra,
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
            created.append(spec.name)  # type: ignore[attr-defined]

        await self.bot.db.set_json_setting(ROLE_KINDS[kind], existing)

        embed = base_embed(
            f"✅ {KIND_LABELS[kind]} 준비 완료",
            Colors.SUCCESS,
            description=(
                "상점에서 팔 역할을 정리했습니다.\n"
                "**봇 역할을 이 역할들보다 위로 올려 주세요.** 아니면 지급되지 않습니다."
            ),
        )
        embed.add_field(
            name=f"새로 만듦 ({len(created)})", value=join_names(created), inline=False
        )
        if reused:
            embed.add_field(
                name=f"기존 역할 사용 ({len(reused)})",
                value=join_names(reused),
                inline=False,
            )
        if renamed:
            embed.add_field(
                name=f"이름 변경 ({len(renamed)})",
                value=join_names(renamed),
                inline=False,
            )
        if stuck:
            embed.add_field(
                name=f"⚠️ 이름을 못 바꾼 역할 ({len(stuck)})",
                value=(
                    join_names(stuck)
                    + "\n봇 역할이 이 역할들보다 아래에 있습니다. "
                    "봇 역할을 위로 올린 뒤 다시 실행해 주세요."
                ),
                inline=False,
            )
        embed.add_field(
            name=f"이미 등록됨 ({len(kept)})", value=join_names(kept), inline=False
        )

        wants_gradient = sum(
            getattr(spec, "secondary", None) is not None for spec in specs.values()
        )
        if wants_gradient:
            if not gradient_ok:
                note = (
                    "이 서버는 아직 그라데이션 역할을 쓸 수 없어 **단색**으로 만들었습니다.\n"
                    "서버 부스트 조건을 채운 뒤 역할을 지우고 다시 실행하면 적용됩니다."
                )
            elif gradient_used:
                note = f"**{wants_gradient}종**에 적용했습니다."
            else:
                note = (
                    f"그라데이션 대상 **{wants_gradient}종**은 이미 만들어져 있어 "
                    "손대지 않았습니다."
                )
            embed.add_field(name="그라데이션", value=note, inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="색상역할생성",
        description="[관리자] 역할상점에서 팔 색상 역할을 만듭니다.",
    )
    @app_commands.default_permissions(manage_guild=True)
    @staff_only()
    async def create_color_roles(self, interaction: discord.Interaction) -> None:
        specs = {key: CARD_THEMES[key] for key in SHOP_THEME_KEYS}
        await self._ensure_roles(interaction, KIND_COLOR_ROLE, specs, prefix="🎨 ")

    @app_commands.command(
        name="응원역할생성",
        description="[관리자] 역할상점에서 팔 LCK 팀 응원 역할을 만듭니다.",
    )
    @app_commands.default_permissions(manage_guild=True)
    @staff_only()
    async def create_team_roles(self, interaction: discord.Interaction) -> None:
        await self._ensure_roles(interaction, KIND_TEAM_ROLE, dict(LCK_TEAMS))

    @app_commands.command(
        name="챔피언역할생성",
        description="[관리자] 챔피언상점에서 팔 챔피언 역할 60종을 만듭니다.",
    )
    @app_commands.default_permissions(manage_guild=True)
    @staff_only()
    async def create_champion_roles(self, interaction: discord.Interaction) -> None:
        # 60개를 한 번에 만들면 레이트 리밋에 걸려 몇 분 걸릴 수 있다.
        await self._ensure_roles(interaction, KIND_CHAMPION_ROLE, dict(CHAMPIONS))

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
        if kind in ROLE_KINDS:
            await self._apply_exclusive_role(member, kind, item_key)

        await self.bot.db.add_points(
            member.id, -price, f"상점 구매 — {KIND_LABELS.get(kind, kind)}"
        )
        # 같은 갈래는 하나만 유지되므로 이전 구매는 여기서 끝낸다.
        # (역할은 이미 갈아 끼웠고, 보유 목록에도 남으면 안 된다)
        await self.bot.db.expire_purchases(member.id, kind)
        expires = await self.bot.db.add_purchase(
            member.id, kind, item_key, price, shop_duration(price), value
        )

        await self._log_purchase(member, kind, item_key, price, value, expires)
        return expires

    async def _apply_exclusive_role(
        self, member: discord.Member, kind: str, key: str
    ) -> None:
        """역할을 지급하고, 같은 갈래에서 전에 산 역할은 회수한다."""
        roles = await self.role_map(kind)
        role_id = roles.get(key)
        role = member.guild.get_role(role_id) if role_id else None
        if role is None:
            command = ROLE_SETUP_COMMANDS[kind]
            raise ShopError(
                f"{KIND_LABELS[kind]}이 아직 준비되지 않았습니다. "
                f"관리자에게 {command} 실행을 요청해 주세요."
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
                await member.add_roles(role, reason=f"상점 {KIND_LABELS[kind]} 구매")
            if others:
                await member.remove_roles(*others, reason=f"상점 {KIND_LABELS[kind]} 변경")
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
        name = item_name(item_key)
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
            for kind in ROLE_KINDS:
                removed = await self._expire_kind(guild, kind)
                if removed:
                    log.info(
                        "[%s] 만료된 %s %d개를 회수했습니다.",
                        guild.name, KIND_LABELS[kind], removed,
                    )

    async def _expire_kind(self, guild: discord.Guild, kind: str) -> int:
        """기간이 끝난 역할을 회수하고 몇 개를 뗐는지 돌려준다."""
        roles = await self.role_map(kind)
        if not roles:
            return 0

        active = {
            (int(row["user_id"]), str(row["item_key"]))
            for row in await self.bot.db.active_by_kind(kind)
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
        return removed

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
                name = item_name(key)
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
            f"아이템은 **{Shop.DURATION_DAYS}일**간 유지되고, "
            f"비싼 것은 더 오래갑니다."
        ),
    )
    embed.add_field(
        name="🎨 역할상점",
        value=(
            f"색상 역할 · {fmt_points(Shop.COLOR_ROLE_PRICE)}\n"
            f"LCK 응원 역할 (그라데이션) · {fmt_points(Shop.TEAM_ROLE_PRICE)}"
        ),
        inline=False,
    )
    embed.add_field(
        name="⚔️ 챔피언상점",
        value=(
            f"챔피언 {len(CHAMPIONS)}종 · {fmt_points(Shop.CHAMPION_ROLE_PRICE)}\n"
            f"{GRADIENT_MARK} 그라데이션 챔피언 "
            f"{sum(c.gradient for c in CHAMPIONS.values())}종 · "
            f"{fmt_points(Shop.CHAMPION_GRADIENT_PRICE)}"
        ),
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
        self.champion_lane: str | None = None  # 챔피언상점에서 고른 라인
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
            colors = await self.cog.role_map(KIND_COLOR_ROLE)
            teams = await self.cog.role_map(KIND_TEAM_ROLE)
            embed = base_embed(
                "🎨 역할상점",
                Colors.GOLD,
                description=(
                    f"보유 포인트 **{fmt_points(balance)}**\n"
                    f"색상 **{shop_duration(Shop.COLOR_ROLE_PRICE)}일** · "
                    f"LCK 응원 **{shop_duration(Shop.TEAM_ROLE_PRICE)}일** 유지."
                ),
            )
            embed.add_field(
                name=f"색상 역할 · {fmt_points(Shop.COLOR_ROLE_PRICE)}",
                value=(
                    " · ".join(
                        CARD_THEMES[k].name for k in colors if k in CARD_THEMES
                    )
                    if colors
                    else "아직 없습니다. 관리자가 `/색상역할생성` 을 실행하면 열립니다."
                ),
                inline=False,
            )
            embed.add_field(
                name=f"LCK 응원 역할 · {fmt_points(Shop.TEAM_ROLE_PRICE)}",
                value=(
                    " · ".join(LCK_TEAMS[k].name for k in teams if k in LCK_TEAMS)
                    if teams
                    else "아직 없습니다. 관리자가 `/응원역할생성` 을 실행하면 열립니다."
                ),
                inline=False,
            )
            embed.set_footer(text="롤 같이 하자 · 각 갈래에서 하나씩 가질 수 있습니다")
            if colors:
                self.add_item(ColorRoleSelect(colors))
            if teams:
                self.add_item(TeamRoleSelect(teams))
            if not colors and not teams:
                embed.color = Colors.DANGER
        elif category == "champion":
            embed = await self._champion_embed(balance)
        else:
            embed = base_embed(
                "🪪 기타상점",
                Colors.TEAL,
                description=(
                    f"`/프로필` 카드를 꾸밉니다. "
                    f"**{shop_duration(Shop.THEME_PRICE)}일** 유지.\n"
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

    async def _champion_embed(self, balance: int) -> discord.Embed:
        """챔피언상점 화면. 선택지가 25개를 넘지 않도록 라인을 먼저 고르게 한다."""
        roles = await self.cog.role_map(KIND_CHAMPION_ROLE)
        embed = base_embed(
            "⚔️ 챔피언상점",
            Colors.TEAL,
            description=(
                f"보유 포인트 **{fmt_points(balance)}** · "
                f"일반 **{shop_duration(Shop.CHAMPION_ROLE_PRICE)}일** · "
                f"그라데이션 **{shop_duration(Shop.CHAMPION_GRADIENT_PRICE)}일** 유지\n"
                f"일반 {fmt_points(Shop.CHAMPION_ROLE_PRICE)} · "
                f"{GRADIENT_MARK} 그라데이션 "
                f"{fmt_points(Shop.CHAMPION_GRADIENT_PRICE)}"
            ),
        )
        if not roles:
            embed.color = Colors.DANGER
            embed.add_field(
                name="아직 열리지 않았습니다",
                value="관리자가 `/챔피언역할생성` 을 실행하면 열립니다.",
                inline=False,
            )
            return embed

        self.add_item(ChampionLaneSelect(self.champion_lane))

        if self.champion_lane is None:
            for lane, short in LANE_SHORT.items():
                names = [
                    f"{GRADIENT_MARK}{spec.name}" if spec.gradient else spec.name
                    for key, spec in champions_in_lane(lane).items()
                    if key in roles
                ]
                if names:
                    embed.add_field(name=short, value=join_names(names), inline=False)
            embed.set_footer(text="롤 같이 하자 · 라인을 고르면 살 수 있습니다")
            return embed

        available = {
            key: spec
            for key, spec in champions_in_lane(self.champion_lane).items()
            if key in roles
        }
        if not available:
            embed.add_field(
                name=LANE_SHORT[self.champion_lane],
                value="이 라인은 아직 준비된 역할이 없습니다.",
                inline=False,
            )
            return embed

        self.add_item(ChampionSelect(available))
        embed.add_field(
            name=f"{LANE_SHORT[self.champion_lane]} · {len(available)}종",
            value=join_names(
                [
                    f"{GRADIENT_MARK}{spec.name}" if spec.gradient else spec.name
                    for spec in available.values()
                ]
            ),
            inline=False,
        )
        embed.set_footer(text="롤 같이 하자 · 챔피언 역할은 하나만 가질 수 있습니다")
        return embed


class CategorySelect(discord.ui.Select):
    def __init__(self, default: str | None = None) -> None:
        super().__init__(
            placeholder="분류를 골라 주세요",
            options=[
                discord.SelectOption(
                    label="역할상점", value="role", emoji="🎨",
                    description="색상 역할 · LCK 응원 역할",
                    default=default == "role",
                ),
                discord.SelectOption(
                    label="챔피언상점", value="champion", emoji="⚔️",
                    description=f"챔피언 역할 {len(CHAMPIONS)}종",
                    default=default == "champion",
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


class ChampionLaneSelect(discord.ui.Select):
    """챔피언 60종을 한 번에 못 보여주므로 라인으로 먼저 좁힌다."""

    def __init__(self, default: str | None = None) -> None:
        super().__init__(
            placeholder="라인 고르기",
            options=[
                discord.SelectOption(
                    label=short,
                    value=lane,
                    description=f"{len(champions_in_lane(lane))}종",
                    default=default == lane,
                )
                for lane, short in LANE_SHORT.items()
            ],
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view: ShopView = self.view  # type: ignore[assignment]
        view.champion_lane = self.values[0]
        await view.show_category(interaction, "champion")


class ThemeSelect(discord.ui.Select):
    def __init__(self) -> None:
        super().__init__(
            placeholder="카드 테마 고르기",
            options=[
                discord.SelectOption(
                    label=CARD_THEMES[key].name,
                    value=key,
                    description=f"{Shop.THEME_PRICE:,}P · {shop_duration(Shop.THEME_PRICE)}일",
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


class RoleSelect(discord.ui.Select):
    """역할 한 개를 골라 바로 사는 선택지. 색상과 응원이 같은 흐름을 쓴다."""

    def __init__(
        self,
        kind: str,
        roles: dict[str, int],
        names: dict[str, str],
        *,
        placeholder: str,
        price: int,
        row: int,
        prices: dict[str, int] | None = None,
    ) -> None:
        self.kind = kind
        self.price = price
        self.prices = prices or {}  # 상품마다 값이 다를 때만 채운다
        super().__init__(
            placeholder=placeholder,
            options=[
                discord.SelectOption(
                    label=names[key],
                    value=key,
                    description=(
                        f"{self.prices.get(key, price):,}P · "
                        f"{shop_duration(self.prices.get(key, price))}일"
                    ),
                )
                for key in roles
                if key in names
            ][:25],
            row=row,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view: ShopView = self.view  # type: ignore[assignment]
        key = self.values[0]
        price = self.prices.get(key, self.price)
        try:
            expires = await view.cog.purchase(view.member, self.kind, key, price)
        except ShopError as exc:
            await interaction.response.send_message(f"❌ {exc}", ephemeral=True)
            return

        balance = await view.cog.bot.db.get_points(view.member.id)
        await interaction.response.send_message(
            embed=base_embed(
                "✅ 구매 완료",
                Colors.SUCCESS,
                description=(
                    f"**{item_name(key)}** {KIND_LABELS[self.kind]}을 받았습니다.\n"
                    f"만료 {fmt_expiry(expires)} · 남은 포인트 {fmt_points(balance)}"
                ),
            ),
            ephemeral=True,
        )


class ColorRoleSelect(RoleSelect):
    def __init__(self, roles: dict[str, int]) -> None:
        super().__init__(
            KIND_COLOR_ROLE,
            roles,
            {k: v.name for k, v in CARD_THEMES.items()},
            placeholder="색상 고르기",
            price=Shop.COLOR_ROLE_PRICE,
            row=1,
        )


class TeamRoleSelect(RoleSelect):
    def __init__(self, roles: dict[str, int]) -> None:
        super().__init__(
            KIND_TEAM_ROLE,
            roles,
            {k: v.name for k, v in LCK_TEAMS.items()},
            placeholder="응원하는 LCK 팀 고르기",
            price=Shop.TEAM_ROLE_PRICE,
            row=2,
        )


class ChampionSelect(RoleSelect):
    """한 라인의 챔피언만 담는다. 그라데이션 챔피언은 값이 따로 붙는다."""

    def __init__(self, champions: dict[str, object]) -> None:
        super().__init__(
            KIND_CHAMPION_ROLE,
            {key: 0 for key in champions},
            {
                key: (
                    f"{GRADIENT_MARK} {spec.name}"  # type: ignore[attr-defined]
                    if spec.gradient  # type: ignore[attr-defined]
                    else spec.name  # type: ignore[attr-defined]
                )
                for key, spec in champions.items()
            },
            placeholder="챔피언 고르기",
            price=Shop.CHAMPION_ROLE_PRICE,
            prices={key: champion_price(key) for key in champions},
            row=2,
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
