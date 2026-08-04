-- Core/Registry.lua
-- Module registry. Mirrors app/modules/registry.py. A "module" is any table
-- that exposes a `name` string and optional lifecycle hooks (OnEnable,
-- OnDisable, OnPlayerUpdate). Modules register themselves at load time.

Hermes.Registry = {}
local Registry = Hermes.Registry

Registry._modules = {}   -- name -> module table
Registry._order = {}     -- insertion order of names

--- Register a module. Safe to call once per module.
--- @param mod table  module table with at least a `name` field
function Registry:register(mod)
    assert(type(mod) == "table", "Registry:register expects a table")
    assert(type(mod.name) == "string", "module must have a string .name")
    if self._modules[mod.name] then
        -- Re-registration is a no-op (helps during /reload while developing).
        return self._modules[mod.name]
    end
    self._modules[mod.name] = mod
    table.insert(self._order, mod.name)
    -- Expose on the Hermes namespace for convenience (Hermes.Rotation, etc.)
    if not Hermes[mod.name] then Hermes[mod.name] = mod end
    return mod
end

--- Get a registered module by name.
function Registry:get(name)
    return self._modules[name]
end

--- Iterate every module, calling fn(mod).
function Registry:each(fn)
    for _, name in ipairs(self._order) do
        fn(self._modules[name])
    end
end

--- Count of registered modules.
function Registry:count()
    return #self._order
end

--- Called once after all addon files have loaded. Invokes OnEnable on each.
function Registry:finalize()
    self:each(function(mod)
        if mod.OnEnable then
            local ok, err = pcall(mod.OnEnable, mod)
            if not ok then
                Hermes:Print("Module '" .. mod.name .. "' failed to enable: " .. tostring(err))
            end
        end
    end)
end
