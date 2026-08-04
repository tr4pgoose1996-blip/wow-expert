-- Modules/Cooldowns.lua
-- Cooldown tracker. Mirrors the backend realtime CooldownFrame. Watches a
-- configured list of core spells and reports which are ready, with WHY each
-- matters (its role in the priority). This is the in-client coach; the backend
-- realtime module streams the same data to the desktop overlay.

local Cooldowns = { name = "Cooldowns" }
Hermes.Registry:register(Cooldowns)

-- SpellIDs to watch, keyed by spec. (Expand like the backend specs.py.)
local WATCH = {
    ["Fury"] = { 184364, 184349, 85288 },          -- Odyn's Fury, Enrage-ish, Rampage
    ["Frost"] = { 51271, 279302, 49020 },          -- Pillar of Frost, Frostwyrm's Fury, Obliterate
    ["Havoc"] = { 188499, 213241 },                -- Blade Dance, Eye Beam
    ["Shadow"] = { 228260, 341273 },               -- Void Eruption, Mind Blast
}

local READY_LABEL = "|cff00ff00READY|r"
local COOLDOWN_LABEL = "|cffff4040CD|r"

function Cooldowns:GetWatched()
    local spec = Hermes.GM and Hermes.GM.context and Hermes.GM.context.profile.favoriteSpec
    return WATCH[spec] or {}
end

--- Return a list of {id, name, ready, remaining} for the watched spells.
function Cooldowns:Tick()
    local out = {}
    for _, id in ipairs(self:GetWatched()) do
        local name = GetSpellInfo(id)
        if name then
            local start, duration = GetSpellCooldown(id)
            local remaining = 0
            if start and duration and duration > 0 then
                remaining = math.max(0, start + duration - GetTime())
            end
            table.insert(out, {
                id = id, name = name,
                ready = remaining <= 0,
                remaining = remaining,
            })
        end
    end
    return out
end

function Cooldowns:contribute(intent, ctx)
    if intent.rotation <= 0 then return nil end
    local ready = self:Tick()
    local readyNames = {}
    for _, c in ipairs(ready) do
        if c.ready then table.insert(readyNames, c.name) end
    end
    if #readyNames == 0 then return nil end
    return {
        {
            title = "Cooldowns ready: " .. table.concat(readyNames, ", "),
            priority = 5,
            effortHours = 0.05,
            source = "rotation",
            why = "Off-cooldown core abilities are your biggest burst windows. Use them now, "
                .. "not after they would expire, to maximize damage per second.",
            actions = readyNames,
        },
    }
end

function Cooldowns:OnEnable()
    -- Poll every 0.5s and push to the Coach frame's cooldown row if visible.
    local f = CreateFrame("Frame")
    f:SetScript("OnUpdate", function(self)
        self.t = (self.t or 0) + arg1
        if self.t >= 0.5 then
            self.t = 0
            if Hermes.CoachFrame and Hermes.CoachFrame:IsShown() then
                Hermes.CoachFrame:UpdateCooldowns(Cooldowns:Tick())
            end
        end
    end)
end
