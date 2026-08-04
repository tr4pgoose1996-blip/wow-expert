-- Modules/Quests.lua
-- Quest guidance. Mirrors app/modules/quests (graph/optimizer/waypoints). In
-- WoW Lua we can read the player's tracked quest objectives and suggest the
-- most efficient next step using quest POI data where available.

local Quests = { name = "Quests" }
Hermes.Registry:register(Quests)

function Quests:contribute(intent, ctx)
    -- Quests contribute when the player asks broadly or about progression.
    if intent.content <= 0 and intent.progress <= 0 then return nil end

    local tracked = {}
    -- Iterate the quest log (max 25 slots in retail).
    for i = 1, 25 do
        local title, _, _, isHeader, _, _, _, _, _, _, _, _, _, _, _, _, isOnMap =
            GetQuestLogTitle(i)
        if title and not isHeader then
            table.insert(tracked, { index = i, title = title, onMap = isOnMap })
        end
    end
    if #tracked == 0 then return nil end

    -- Recommend the first quest that is shown on the map (closest objective).
    local nextQ = nil
    for _, q in ipairs(tracked) do
        if q.onMap then nextQ = q; break end
    end
    nextQ = nextQ or tracked[1]

    return {
        {
            title = "Continue quest: " .. nextQ.title,
            priority = 3,
            effortHours = 0.5,
            source = "quests",
            why = "This quest has an active map marker, so the objective is nearest. Clearing it "
                .. "keeps your quest log tidy and your reputation/catch-up progress moving.",
            actions = { nextQ.title },
        },
    }
end

function Quests:Show()
    local n = 0
    for i = 1, 25 do
        local title, _, _, isHeader = GetQuestLogTitle(i)
        if title and not isHeader then
            n = n + 1
            Hermes:Print("  " .. (isHeader and "[Zone] " or "") .. title)
        end
    end
    if n == 0 then Hermes:Print("Quest log is empty.") end
end
