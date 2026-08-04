-- Modules/Rotation.lua
-- Rotation advisor. Mirrors app/modules/rotation. In WoW Lua we cannot execute
-- the full priority engine, but we surface the player's known priority (from a
-- per-spec table) and flag off-cooldown core abilities. The heavy engine lives
-- in the backend; this is the in-client coach.

local Rotation = { name = "Rotation" }
Hermes.Registry:register(Rotation)

-- Per-spec priority strings (illustrative; expand per spec like the backend's specs.py).
local PRIORITIES = {
    ["Fury"] = { "Odyn's Fury", "Rampage (>=80-100 rage)", "Execute", "Bloodthirst", "Raging Blow" },
    ["Frost"] = { "Pillar of Frost", "Frostwyrm's Fury", "Obliterate", "Howling Blast", "Remorseless Winter" },
    ["Havoc"] = { "Eye Beam / Death Sweep", "Blade Dance", "Demon's Bite", "Throw Glaive" },
    ["Shadow"] = { "Voidform", "Devastation", "Mind Blast", "Mind Flay: Insanity", "Vampiric Touch" },
}

function Rotation:GetPriority(spec)
    spec = spec or (Hermes.GM and Hermes.GM.context and Hermes.GM.context.profile.favoriteSpec)
    return PRIORITIES[spec] or nil
end

--- Contribute objectives to the GM plan for rotation-related intent.
function Rotation:contribute(intent, ctx)
    if intent.rotation <= 0 and intent.learn <= 0 then return nil end
    local spec = ctx.profile.favoriteSpec
    local prio = self:GetPriority(spec)
    if not prio then return nil end
    return {
        {
            title = "Follow your " .. (spec or "current") .. " priority: " .. table.concat(prio, " → "),
            priority = 4,
            effortHours = 0.25,
            source = "rotation",
            why = "Your DPS climbs most from pressing the highest-priority ability that is ready. "
                .. "A tight rotation beats gear upgrades until you are near 100% optimal uptime.",
            actions = prio,
        },
    }
end

--- Show the priority in chat (slash: /hermes rotation).
function Rotation:Show()
    local spec = Hermes.GM and Hermes.GM.context and Hermes.GM.context.profile.favoriteSpec
    local prio = self:GetPriority(spec)
    if not prio then
        Hermes:Print("No priority table for spec '" .. tostring(spec) .. "'. Set it with /hermes profile spec=<Spec>.")
        return
    end
    Hermes:Print("Rotation priority (" .. spec .. "):")
    for i, step in ipairs(prio) do
        Hermes:Print("  " .. i .. ". " .. step)
    end
end

function Rotation:OnEnable()
    -- Hook a lightweight rotation hint into the Coach frame if available.
end
