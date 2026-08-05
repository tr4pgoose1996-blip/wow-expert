-- Modules/Collections.lua
-- Collection Tracker. Mirrors app/modules/collections. Reminds the player of
-- account-wide collectibles they can still target, filtered by class/spec.

local Collections = { name = "Collections" }
Hermes.Registry:register(Collections)

-- Illustrative fastest-obtainable goals (expand like backend domain.py).
-- visibleFor: nil = all classes; otherwise only those classes see it.
local GOALS = {
    { id = "pony",     name = "Swift White Steed (store/Promotions)", kind = "mount",
      eta = "account-bound, instant if claimed", visibleFor = nil,
      why = "Account-wide mount — one-time claim, usable on every character forever." },
    { id = "example",  name = "Collect a world-drop mount", kind = "mount",
      eta = "~1-3 hours farming", visibleFor = nil,
      why = "World-drop mounts are account-wide and a fast prestige win." },
    { id = "druidform", name = "Druid Artifact Appearances", kind = "appearance",
      eta = "spec progression", visibleFor = { "Druid" },
      why = "Druid-only appearances; progress your spec to unlock them." },
}

function Collections:Visible(ctx)
    local cls = ctx and ctx.class
    local out = {}
    for _, g in ipairs(GOALS) do
        if not g.visibleFor or (cls and tContains(g.visibleFor, cls)) then
            table.insert(out, g)
        end
    end
    return out
end

function Collections:contribute(intent, ctx)
    if intent.collect <= 0 then return nil end
    local visible = self:Visible(ctx)
    if #visible == 0 then return nil end
    local objs = {}
    for _, g in ipairs(visible) do
        table.insert(objs, {
            title = "Collect: " .. g.name .. " (" .. g.kind .. ")",
            priority = 2,
            effortHours = 1.0,
            source = "collections",
            why = g.why .. " ETA: " .. g.eta .. ".",
            actions = { g.name },
        })
    end
    return objs
end

function Collections:Show()
    local visible = self:Visible(Hermes.GM and Hermes.GM.context)
    if #visible == 0 then Hermes:Print("No active collection goals.") return end
    Hermes:Print("Collection reminders:")
    for _, g in ipairs(visible) do
        Hermes:Print("  " .. g.name .. " — " .. g.why)
    end
end
