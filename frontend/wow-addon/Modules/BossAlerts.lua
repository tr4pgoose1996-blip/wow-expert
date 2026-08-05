-- Modules/BossAlerts.lua
-- Boss / dungeon / Mythic+ affix coach. Mirrors app/modules/coach. Provides
-- taught guidance for the current instance/affix. The backend coach holds the
-- full per-boss encyclopedia; here we surface the key mechanic + WHY.

local BossAlerts = { name = "BossAlerts" }
Hermes.Registry:register(BossAlerts)

-- Affix cheat-sheet (expand like backend coach/_AFFIX_TEACHING).
local AFFIXES = {
    [10] = { name = "Fortified", why = "Trash hits harder — use CC and pull in small packs to avoid deaths." },
    [11] = { name = "Tyrannical", why = "Bosses have more health — save major cooldowns for boss burn." },
    [12] = { name = "Bolstering", why = "Killing trash unevenly empowers survivors — even out your damage." },
    [13] = { name = "Bursting", why = "Stacking DoT on death — stagger kills or dispel/heals to survive." },
    [14] = { name = "Raging", why = "Enraged trash can't be CC'd — focus them down fast." },
    [15] = { name = "Sanguine", why = "Healing pools on death — kite away from corpses." },
    [16] = { name = "Teeming", why = "Extra trash — plan routes to skip non-essential packs." },
    [17] = { name = "Volcanic", why = "Avoid ground circles — keep moving during caster phases." },
}

-- A small boss tip table (expand per dungeon like the backend).
local BOSS_TIPS = {
    ["The Stoneborn"] = "Interrupt the cast; bait the charge to the wall.",
    ["Devour"] = "Spread for the cone; soak the small add before the big one.",
}

function BossAlerts:CurrentAffixTips()
    local tips = {}
    -- Mythic+ affixes are discoverable via C_ChallengeMode.GetAffixInfo in retail;
    -- we guard for the API and fall back gracefully.
    if C_ChallengeMode and C_ChallengeMode.GetAffixInfo then
        for i = 1, 4 do
            local id = C_ChallengeMode.GetAffixInfo(i)
            if id and AFFIXES[id] then table.insert(tips, AFFIXES[id]) end
        end
    end
    return tips
end

function BossAlerts:contribute(intent, ctx)
    if intent.content <= 0 then return nil end
    local tips = self:CurrentAffixTips()
    local zone = ctx.zone or ""
    local bossTip = BOSS_TIPS[zone]
    local objs = {}
    for _, t in ipairs(tips) do
        table.insert(objs, {
            title = "Affix — " .. t.name,
            priority = 3,
            effortHours = 0.1,
            source = "coach",
            why = t.why,
            actions = { t.name },
        })
    end
    if bossTip then
        table.insert(objs, {
            title = "Boss — " .. zone,
            priority = 4,
            effortHours = 0.1,
            source = "coach",
            why = bossTip,
            actions = { bossTip },
        })
    end
    return (#objs > 0) and objs or nil
end

function BossAlerts:Show()
    local tips = self:CurrentAffixTips()
    if #tips == 0 then
        Hermes:Print("No active Mythic+ affixes (or not in a key).")
        return
    end
    Hermes:Print("Current affixes:")
    for _, t in ipairs(tips) do
        Hermes:Print("  " .. t.name .. ": " .. t.why)
    end
end
