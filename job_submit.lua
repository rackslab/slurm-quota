#!/usr/bin/env lua
--[[
Slurm Quota Job Submit Script

Copyright (c) 2025 Rackslab
Copyright (c) 2025 Université de Montpellier
SPDX-License-Identifier: GPL-2.0-or-later

This script is designed to be used as a Slurm job_submit plugin to enforce
users & accounts CPU minute quotas before job submission or modification.
]]

local DBI = require("DBI")
local posix = require("posix")

-- Configuration
local DB_PATH = "/var/lib/state/slurm-quota/slurm-quota.db"
-- Reject jobs whose user/account association is not defined in Slurm, based on
-- the associations cache synchronized by slurm-quota-sync. Associations must be
-- synchronized before enabling this script, otherwise all jobs are refused.
local CHECK_ASSOCIATIONS = true

-- Initialize random number generator
math.randomseed(os.time())

-- Generate a UUID v4
local function generate_uuid()
    local template = "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx"
    return string.gsub(template, "[xy]", function(c)
        local v = (c == "x") and math.random(0, 0xf) or math.random(0, 0x3) + 0x8
        return string.format("%x", v)
    end)
end

-- Slurm value of numeric fields not provided in job descriptions (NO_VAL)
local NO_VAL = slurm.NO_VAL

-- Check whether a job description numeric field is not provided
local function is_unset(value)
    return value == nil or value == NO_VAL
end

-- Normalize Slurm special values for task count
local function normalize_num_tasks(num_tasks)
    -- Slurm uses NO_VAL for special/unspecified; treat as 1 task for quota
    if num_tasks == NO_VAL then
        return 1
    end
    if not num_tasks or num_tasks <= 0 then
        return 1
    end
    return num_tasks
end

-- Get number of nodes from job description
local function normalize_num_nodes(max_nodes)
    -- Slurm uses NO_VAL for unspecified; treat as 1 node
    if max_nodes == NO_VAL then
        return 1
    end
    if not max_nodes or max_nodes <= 0 then
        return 1
    end
    return max_nodes
end

-- Slurm value of unlimited time limits (INFINITE)
local INFINITE = slurm.INFINITE

-- Get the records of the partitions of a job from the partitions list given by
-- Slurm to the callbacks, indexed by partition name. The partition of the job
-- is a comma-separated list of partition names, or the default partition when
-- not provided.
local function job_partitions(partition, part_list)
    local parts = {}
    if type(part_list) ~= "table" then
        return parts
    end
    if partition and partition ~= "" then
        for name in string.gmatch(partition, "[^,%s]+") do
            if part_list[name] then
                table.insert(parts, part_list[name])
            end
        end
    else
        for _, part in pairs(part_list) do
            if part.flag_default == 1 then
                table.insert(parts, part)
            end
        end
    end
    return parts
end

-- Resolve the time limit of a job (in minutes) for its preallocation. When the
-- time limit is not provided, Slurm sets the DefaultTime of the partition, or
-- its MaxTime when DefaultTime is not set. An unlimited time limit is bounded
-- by the MaxTime of the partition, the job cannot run longer. When the job is
-- submitted to several partitions, the highest limit is retained. Returns nil
-- when the time limit is unlimited and cannot be bounded by any partition.
local function resolve_time_limit(time_limit, partition, part_list)
    if not is_unset(time_limit) and time_limit ~= INFINITE then
        return time_limit
    end
    local resolved = nil
    for _, part in ipairs(job_partitions(partition, part_list)) do
        local limit = part.max_time
        if is_unset(time_limit) and not is_unset(part.default_time) then
            limit = part.default_time
        end
        if is_unset(limit) or limit == INFINITE then
            return nil
        end
        if not resolved or limit > resolved then
            resolved = limit
        end
    end
    return resolved
end

-- Get the name of the first limited quota among the given user and account
-- CPU and GPU quotas, GPU quotas being considered only when GPUs are
-- requested. Returns nil when all these quotas are unlimited.
local function limited_quota(user_data, account_data, gpu_requested)
    if user_data.quota ~= -1 then
        return "user"
    end
    if account_data.quota ~= -1 then
        return "account"
    end
    if gpu_requested and user_data.quota_gpu ~= -1 then
        return "user GPU"
    end
    if gpu_requested and account_data.quota_gpu ~= -1 then
        return "account GPU"
    end
    return nil
end

-- Parse Slurm array index string and count the number of jobs
-- Returns the number of jobs in the array (1 for non-array jobs), or nil on error
-- Handles formats:
--   nil -> 1 (single job)
--   "0-10" -> 11 jobs (range)
--   "0,4,8,12" -> 4 jobs (list)
--   "0,6,16-32" -> mixed (list + range)
--   "0-15:4" -> 4 jobs (divided range: 0,4,8,12)
--   "0-15%4" -> 16 jobs (window limit doesn't affect count)
local function count_array_jobs(array_inx)
    -- If nil or empty, it's a single job
    if not array_inx or array_inx == "" then
        return 1
    end

    local count = 0
    local seen = {}  -- Track seen indices to avoid duplicates

    -- Split by comma to handle lists
    for part in string.gmatch(array_inx, "([^,]+)") do
        -- Trim whitespace
        part = string.match(part, "^%s*(.*)%s*$") or part

        -- Remove window limit (e.g., "0-15%4" -> "0-15")
        part = string.gsub(tostring(part), "%%[0-9]+", "")

        -- Check for divided range (e.g., "0-15:4")
        local range_match, step_match = string.match(part, "^(%d+%-%d+):(%d+)$")
        if range_match and step_match then
            -- Parse divided range
            local start_str, end_str = string.match(range_match, "^(%d+)-(%d+)$")
            if start_str and end_str then
                local start = tonumber(start_str)
                local step = tonumber(step_match)
                local finish = tonumber(end_str)
                if start and step and finish and step > 0 then
                    if start > finish then
                        slurm.log_error(
                            "slurm_job_submit: invalid array range '%s' (start > end)",
                            tostring(part)
                        )
                        return nil
                    end
                    for idx = start, finish, step do
                        if not seen[idx] then
                            seen[idx] = true
                            count = count + 1
                        end
                    end
                end
            end
        -- Check for simple range (e.g., "0-10")
        elseif string.find(part, "^%d+-%d+$") then
            local start_str, end_str = string.match(part, "^(%d+)-(%d+)$")
            if start_str and end_str then
                local start = tonumber(start_str)
                local finish = tonumber(end_str)
                if start and finish then
                    if start > finish then
                        slurm.log_error(
                            "slurm_job_submit: invalid array range '%s' (start > end)",
                            tostring(part)
                        )
                        return nil
                    end
                    for idx = start, finish do
                        if not seen[idx] then
                            seen[idx] = true
                            count = count + 1
                        end
                    end
                end
            end
        -- Single number (e.g., "5")
        elseif string.find(part, "^%d+$") then
            local idx = tonumber(part)
            if idx and not seen[idx] then
                seen[idx] = true
                count = count + 1
            end
        end
    end

    -- If no jobs were counted (malformed string), treat as single job
    return math.max(1, count)
end

-- Resolve effective account from job description
local function resolve_account(job_desc)
    local account = job_desc.account
    if not account or account == "" then
        account = job_desc.default_account
    end
    if not account or account == "" then
        return nil
    end
    return account
end

-- Open SQLite connection helper
local function connect_db()
    local conn, err = DBI.Connect('SQLite3', DB_PATH)
    if conn then
        -- Per-connection settings only: busy_timeout and foreign_keys must be set
        -- on every connection. WAL (journal_mode) is not set here because it is already
        -- enabled by slurm-quota-serve, slurm-quota-charge, or slurm-quota-migrate and
        -- it is persisted in the database file.
        pcall(function()
            local stmt = conn:prepare("PRAGMA busy_timeout = 5000")
            if stmt then
                stmt:execute()
                stmt:close()
            end
            stmt = conn:prepare("PRAGMA foreign_keys = ON")
            if stmt then
                stmt:execute()
                stmt:close()
            end
        end)
    end
    return conn, err
end

-- Check if database exists and is accessible
local function database_exists()
    local file = io.open(DB_PATH, "r")
    if file then
        file:close()
        return true
    end
    return false
end

-- Retry helpers for transient SQLite lock/busy errors
local MAX_DB_RETRIES = 5
local INITIAL_BACKOFF_SEC = 1
local DEFAULT_QUOTA_SETTINGS = {
    default_user_quota_cpu_minutes = -1,
    default_user_quota_gpu_minutes = -1,
    default_account_quota_cpu_minutes = -1,
    default_account_quota_gpu_minutes = -1
}

local function sleep_seconds(seconds)
    -- Sleep using Lua POSIX to avoid spawning a shell
    posix.sleep(tonumber(seconds) or 0)
end

local function is_locked_error(errmsg)
    if not errmsg then
        return false
    end
    local lower = string.lower(tostring(errmsg))
    return string.find(lower, "locked", 1, true) or string.find(lower, "busy", 1, true)
end

local function execute_with_retry(stmt, ...)
    local attempt = 1
    local backoff = INITIAL_BACKOFF_SEC
    while attempt <= MAX_DB_RETRIES do
        local ok, err = stmt:execute(...)
        if ok then
            return ok, nil
        end
        slurm.log_error("slurm_job_submit: failed to execute statement: " .. tostring(err))
        if is_locked_error(err) then
            slurm.log_info("slurm_job_submit: DB locked on execute; retry %d/%d", attempt, MAX_DB_RETRIES)
            sleep_seconds(backoff)
            backoff = math.min(backoff * 2, 8)
            attempt = attempt + 1
        else
            return nil, err
        end
    end
    return nil, "database is locked"
end

-- Check the user/account association exists in the Slurm associations cache.
-- Returns true or false, or nil when the check cannot be performed (database
-- error).
local function association_exists(conn, username, account)
    local stmt, perr = conn:prepare("SELECT 1 AS found FROM slurm_associations WHERE username = ? AND account = ?")
    if not stmt then
        slurm.log_error("slurm_job_submit: failed to prepare statement: " .. tostring(perr))
        return nil
    end
    local ok, exerr = execute_with_retry(stmt, username, account)
    if not ok then
        slurm.log_error("slurm_job_submit: failed to check association: " .. tostring(exerr))
        stmt:close()
        return nil
    end
    local row = stmt:fetch(true)
    stmt:close()
    return row ~= nil
end

-- Load default quotas used for newly created users/accounts
local function load_default_quota_settings(conn)
    if not conn then
        slurm.log_error("slurm_job_submit: load_default_quota_settings called with nil connection")
        return {
            user_cpu = DEFAULT_QUOTA_SETTINGS.default_user_quota_cpu_minutes,
            user_gpu = DEFAULT_QUOTA_SETTINGS.default_user_quota_gpu_minutes,
            account_cpu = DEFAULT_QUOTA_SETTINGS.default_account_quota_cpu_minutes,
            account_gpu = DEFAULT_QUOTA_SETTINGS.default_account_quota_gpu_minutes
        }
    end

    local defaults = {
        user_cpu = DEFAULT_QUOTA_SETTINGS.default_user_quota_cpu_minutes,
        user_gpu = DEFAULT_QUOTA_SETTINGS.default_user_quota_gpu_minutes,
        account_cpu = DEFAULT_QUOTA_SETTINGS.default_account_quota_cpu_minutes,
        account_gpu = DEFAULT_QUOTA_SETTINGS.default_account_quota_gpu_minutes
    }

    local stmt, perr = conn:prepare("SELECT key AS key, value AS value FROM settings WHERE key IN (?, ?, ?, ?)")
    if not stmt then
        slurm.log_error("slurm_job_submit: failed to prepare default settings query: " .. tostring(perr))
        return defaults
    end

    local ok, exerr = execute_with_retry(
        stmt,
        "default_user_quota_cpu_minutes",
        "default_user_quota_gpu_minutes",
        "default_account_quota_cpu_minutes",
        "default_account_quota_gpu_minutes"
    )
    if not ok then
        slurm.log_error("slurm_job_submit: failed to execute default settings query: " .. tostring(exerr))
        stmt:close()
        return defaults
    end

    while true do
        local row = stmt:fetch(true)
        if not row then
            break
        end
        local key = row.key
        local value = tonumber(row.value)
        if value ~= nil then
            if key == "default_user_quota_cpu_minutes" then
                defaults.user_cpu = value
            elseif key == "default_user_quota_gpu_minutes" then
                defaults.user_gpu = value
            elseif key == "default_account_quota_cpu_minutes" then
                defaults.account_cpu = value
            elseif key == "default_account_quota_gpu_minutes" then
                defaults.account_gpu = value
            end
        end
    end

    stmt:close()
    return defaults
end

-- Remaining quota in seconds. Quotas are expressed in minutes, consumption and
-- preallocations in seconds. Comparing in seconds avoids rounding.
local function available_seconds(quota_minutes, consumed_seconds, preallocated_seconds)
    return math.max(0, quota_minutes * 60 - consumed_seconds - preallocated_seconds)
end

-- Seconds rounded up to whole minutes, for messages.
local function ceil_minutes(seconds)
    return math.ceil(seconds / 60)
end

-- Get or create user in database
local function get_or_create_user(conn, username)
    if not conn then
        slurm.log_error("slurm_job_submit: get_or_create_user called with nil connection")
        return nil
    end

    -- Check if user exists
    local stmt, perr = conn:prepare("SELECT COALESCE((SELECT SUM(c.consumed_cpu_seconds) FROM user_account_consumption c WHERE c.username = e.username), 0) AS consumed_s, quota_cpu_minutes AS quota, COALESCE((SELECT SUM(c.consumed_gpu_seconds) FROM user_account_consumption c WHERE c.username = e.username), 0) AS consumed_gpu_s, quota_gpu_minutes AS quota_gpu FROM users e WHERE e.username = ?")
    if not stmt then
        slurm.log_error("slurm_job_submit: failed to prepare statement: " .. tostring(perr))
        return nil
    end

    local ok, exerr = execute_with_retry(stmt, username)
    if not ok then
        slurm.log_error("slurm_job_submit: failed to execute select: " .. tostring(exerr))
        stmt:close()
        return nil
    end

    local row = stmt:fetch(true)
    stmt:close()

    if row then
        return {
            consumed_s = row.consumed_s or 0,
            quota = row.quota or -1,
            consumed_gpu_s = row.consumed_gpu_s or 0,
            quota_gpu = row.quota_gpu or -1
        }
    else
        local default_quotas = load_default_quota_settings(conn)
        -- User doesn't exist, create it
        local insert_stmt, ierr = conn:prepare("INSERT INTO users (username, quota_cpu_minutes, quota_gpu_minutes) VALUES (?, ?, ?)")
        if not insert_stmt then
            slurm.log_error("slurm_job_submit: failed to prepare insert statement: " .. tostring(ierr))
            return nil
        end

        local default_user_cpu = (default_quotas and default_quotas.user_cpu) or -1
        local default_user_gpu = (default_quotas and default_quotas.user_gpu) or -1
        local ok2, exerr2 = execute_with_retry(insert_stmt, username, default_user_cpu, default_user_gpu)
        if not ok2 then
            slurm.log_error("slurm_job_submit: failed to create user: " .. tostring(exerr2))
            insert_stmt:close()
            return nil
        end

        insert_stmt:close()

        slurm.log_info("slurm_job_submit: created new user: " .. username)
        return {
            consumed_s = 0,
            quota = default_user_cpu,
            consumed_gpu_s = 0,
            quota_gpu = default_user_gpu
        }
    end
end

-- Get or create account in database
local function get_or_create_account(conn, account)
    if not conn then
        slurm.log_error("slurm_job_submit: get_or_create_account called with nil connection")
        return nil
    end

    local stmt, perr = conn:prepare("SELECT COALESCE((SELECT SUM(c.consumed_cpu_seconds) FROM user_account_consumption c WHERE c.account = e.account), 0) AS consumed_s, quota_cpu_minutes AS quota, COALESCE((SELECT SUM(c.consumed_gpu_seconds) FROM user_account_consumption c WHERE c.account = e.account), 0) AS consumed_gpu_s, quota_gpu_minutes AS quota_gpu FROM accounts e WHERE e.account = ?")
    if not stmt then
        slurm.log_error("slurm_job_submit: failed to prepare statement: " .. tostring(perr))
        return nil
    end

    local ok, exerr = execute_with_retry(stmt, account)
    if not ok then
        slurm.log_error("slurm_job_submit: failed to execute select: " .. tostring(exerr))
        stmt:close()
        return nil
    end

    local row = stmt:fetch(true)
    stmt:close()

    if row then
        return {
            consumed_s = row.consumed_s or 0,
            quota = row.quota or -1,
            consumed_gpu_s = row.consumed_gpu_s or 0,
            quota_gpu = row.quota_gpu or -1
        }
    else
        local default_quotas = load_default_quota_settings(conn)
        local insert_stmt, ierr = conn:prepare("INSERT INTO accounts (account, quota_cpu_minutes, quota_gpu_minutes) VALUES (?, ?, ?)")
        if not insert_stmt then
            slurm.log_error("slurm_job_submit: failed to prepare insert statement: " .. tostring(ierr))
            return nil
        end
        local default_account_cpu = (default_quotas and default_quotas.account_cpu) or -1
        local default_account_gpu = (default_quotas and default_quotas.account_gpu) or -1
        local ok2, exerr2 = execute_with_retry(insert_stmt, account, default_account_cpu, default_account_gpu)
        if not ok2 then
            slurm.log_error("slurm_job_submit: failed to create account: " .. tostring(exerr2))
            insert_stmt:close()
            return nil
        end
        insert_stmt:close()
        slurm.log_info("slurm_job_submit: created new account: " .. account)
        return {
            consumed_s = 0,
            quota = default_account_cpu,
            consumed_gpu_s = 0,
            quota_gpu = default_account_gpu
        }
    end
end

-- Load GPU type factors from database (gpu_factors table)
-- If conn is provided, use it; otherwise open a new connection
-- Returns nil on error, or a table with factors on success
local function load_gpu_factors(conn)
    if not conn then
        slurm.log_error("slurm_job_submit: load_gpu_factors called with nil connection")
        return nil
    end

    local factors = {}
    local default_factor = 1.0

    local stmt, perr = conn:prepare("SELECT gpu_type AS gpu_type, factor AS factor FROM gpu_factors")
    if not stmt then
        slurm.log_error("slurm_job_submit: failed to prepare gpu_factors query: " .. tostring(perr))
        return nil
    end

    local ok, exerr = execute_with_retry(stmt)
    if not ok then
        slurm.log_error("slurm_job_submit: failed to execute gpu_factors query: " .. tostring(exerr))
        stmt:close()
        return nil
    end

    while true do
        local row = stmt:fetch(true)
        if not row then
            break
        end
        local gpu_type = row.gpu_type
        local factor = tonumber(row.factor)
        if gpu_type == "default" then
            if factor and factor >= 0 then
                default_factor = factor
            else
                slurm.log_error("slurm_job_submit: invalid default GPU factor: " .. tostring(row.factor))
            end
        elseif gpu_type and factor and factor >= 0 then
            factors[gpu_type] = factor
        else
            slurm.log_error("slurm_job_submit: invalid GPU factor entry: type=" .. tostring(gpu_type) .. " factor=" .. tostring(row.factor))
        end
    end

    stmt:close()

    -- Store default factor
    factors["__default__"] = default_factor
    return factors
end

-- Parse TRES fields (tres_per_job, tres_per_task, tres_per_node, tres_per_socket) to extract GPU types and counts
-- Handles formats:
--   nil or empty -> {}
--   "gres/gpu:h100:2,gres/gpu:h200:1" -> {["h100"] = 2, ["h200"] = 1}
-- Returns: gpu_counts table, error_message (nil if success, string if error)
-- Enforces that GPU type must be present - rejects jobs with gres/gpu:COUNT (without type)
-- Multipliers:
--   tres_per_job: no multiplier (already per job)
--   tres_per_task: multiply by num_tasks
--   tres_per_node: multiply by num_nodes
--   tres_per_socket: multiply by num_tasks (sockets cannot be determined in advance)
local function parse_tres_fields(job_desc, num_tasks, num_nodes)
    local gpu_counts = {}

    -- Define TRES fields with their corresponding multipliers
    local tres_field_configs = {
        {field = job_desc.tres_per_job, multiplier = 1},
        {field = job_desc.tres_per_task, multiplier = num_tasks},
        {field = job_desc.tres_per_node, multiplier = num_nodes},
        {field = job_desc.tres_per_socket, multiplier = num_tasks}
    }

    for _, config in ipairs(tres_field_configs) do
        local tres_str = config.field
        local multiplier = config.multiplier

        if tres_str and tres_str ~= "" then
            -- Split by comma to handle multiple TRES entries
            for item in string.gmatch(tres_str, "([^,]+)") do
                -- Trim whitespace
                item = string.match(item, "^%s*(.-)%s*$") or item
                -- Match pattern: gres/gpu:TYPE=COUNT (with type - required)
                local gpu_type, count_str = string.match(item, "gres/gpu:([^:]+)[:=](%d+)")
                if gpu_type and count_str then
                    local count = tonumber(count_str)
                    if count then
                        gpu_type = string.match(gpu_type, "^%s*(.-)%s*$") or gpu_type  -- trim
                        if gpu_type == "" then
                            -- GPU type is missing - this is an error
                            return nil, "GPU type must be specified (found gres/gpu without type)"
                        end
                        -- Apply multiplier based on TRES field type
                        local total_count = count * multiplier
                        gpu_counts[gpu_type] = (gpu_counts[gpu_type] or 0) + total_count
                    end
                elseif string.find(item, "^gres/gpu[:=]") then
                    -- Found gres/gpu=COUNT without type - reject
                    return nil, "GPU type must be specified (found gres/gpu without type)"
                end
            end
        end
    end

    return gpu_counts, nil
end

-- Calculate preallocated GPU seconds based on GPU counts, time limit (in
-- minutes) and factors. Rounded up so that the preallocation is never lower
-- than the charged consumption.
local function calculate_gpu_seconds(gpu_counts, time_limit, factors)
    local default_factor = factors["__default__"] or 1.0
    local total_gpu_seconds = 0.0

    for gpu_type, count in pairs(gpu_counts) do
        local factor = factors[gpu_type] or default_factor
        total_gpu_seconds = total_gpu_seconds + count * time_limit * 60 * factor
    end

    return math.ceil(total_gpu_seconds)
end

-- Create job record with preallocated CPU and GPU seconds
local function create_job_record(conn, job_uuid, username, account, preallocated_cpu_seconds, preallocated_gpu_seconds, array_size)
    if not conn then
        slurm.log_error("slurm_job_submit: create_job_record called with nil connection")
        return false
    end

    local stmt, perr = conn:prepare("INSERT INTO jobs_preallocations (job_uuid, username, account, preallocated_cpu_seconds, preallocated_gpu_seconds, array_size) VALUES (?, ?, ?, ?, ?, ?)")
    if not stmt then
        slurm.log_error("slurm_job_submit: failed to prepare insert statement: " .. tostring(perr))
        return false
    end

    local ok, exerr = execute_with_retry(stmt, job_uuid, username, account, preallocated_cpu_seconds, preallocated_gpu_seconds, array_size)
    if not ok then
        slurm.log_error("slurm_job_submit: failed to create job record: " .. tostring(exerr))
        stmt:close()
        return false
    end

    stmt:close()
    return true
end

-- Get the preallocation of a job, or nil if not found or on error
local function get_job_preallocation(conn, job_uuid)
    local stmt, perr = conn:prepare("SELECT preallocated_cpu_seconds AS cpu_seconds, preallocated_gpu_seconds AS gpu_seconds, array_size AS array_size FROM jobs_preallocations WHERE job_uuid = ?")
    if not stmt then
        slurm.log_error("slurm_job_modify: failed to prepare statement: " .. tostring(perr))
        return nil
    end
    local ok, exerr = execute_with_retry(stmt, job_uuid)
    if not ok then
        slurm.log_error("slurm_job_modify: failed to get job preallocation: " .. tostring(exerr))
        stmt:close()
        return nil
    end
    local row = stmt:fetch(true)
    stmt:close()
    if not row then
        return nil
    end
    return {
        cpu_seconds = tonumber(row.cpu_seconds) or 0,
        gpu_seconds = tonumber(row.gpu_seconds) or 0,
        array_size = tonumber(row.array_size) or 1
    }
end

-- Update job preallocation
local function update_job_preallocation(conn, job_uuid, account, preallocated_cpu_seconds, preallocated_gpu_seconds, array_size)
    if not conn then
        return false
    end

    local stmt, uerr = conn:prepare("UPDATE jobs_preallocations SET account = ?, preallocated_cpu_seconds = ?, preallocated_gpu_seconds = ?, array_size = ? WHERE job_uuid = ?")
    if not stmt then
        return false
    end

    local updated = execute_with_retry(stmt, account, preallocated_cpu_seconds, preallocated_gpu_seconds or 0, array_size or 1, job_uuid)
    stmt:close()
    return updated and true or false
end

-- Compute current preallocated seconds for a given user
local function get_current_preallocated_for_user(conn, q_username, exclude_job_uuid)
    local sql
    if exclude_job_uuid and exclude_job_uuid ~= "" then
        sql = "SELECT COALESCE(SUM(preallocated_cpu_seconds * COALESCE(array_size, 1)), 0) AS total FROM jobs_preallocations WHERE username = ? AND job_uuid != ?"
    else
        sql = "SELECT COALESCE(SUM(preallocated_cpu_seconds * COALESCE(array_size, 1)), 0) AS total FROM jobs_preallocations WHERE username = ?"
    end
    local stmt, perr = conn:prepare(sql)
    if not stmt then
        return 0
    end
    local ok
    if exclude_job_uuid and exclude_job_uuid ~= "" then
        ok = execute_with_retry(stmt, q_username, exclude_job_uuid)
    else
        ok = execute_with_retry(stmt, q_username)
    end
    if not ok then
        stmt:close()
        return 0
    end
    local row = stmt:fetch(true)
    stmt:close()
    return (row and row.total) or 0
end

-- Compute current preallocated seconds for a given account
local function get_current_preallocated_for_account(conn, q_account, exclude_job_uuid)
    local sql
    if exclude_job_uuid and exclude_job_uuid ~= "" then
        sql = "SELECT COALESCE(SUM(preallocated_cpu_seconds * COALESCE(array_size, 1)), 0) AS total FROM jobs_preallocations WHERE account = ? AND job_uuid != ?"
    else
        sql = "SELECT COALESCE(SUM(preallocated_cpu_seconds * COALESCE(array_size, 1)), 0) AS total FROM jobs_preallocations WHERE account = ?"
    end
    local stmt, perr = conn:prepare(sql)
    if not stmt then
        return 0
    end
    local ok
    if exclude_job_uuid and exclude_job_uuid ~= "" then
        ok = execute_with_retry(stmt, q_account, exclude_job_uuid)
    else
        ok = execute_with_retry(stmt, q_account)
    end
    if not ok then
        stmt:close()
        return 0
    end
    local row = stmt:fetch(true)
    stmt:close()
    return (row and row.total) or 0
end

-- Compute current preallocated GPU seconds for a given user
local function get_current_preallocated_gpu_for_user(conn, q_username, exclude_job_uuid)
    local sql
    if exclude_job_uuid and exclude_job_uuid ~= "" then
        sql = "SELECT COALESCE(SUM(preallocated_gpu_seconds * COALESCE(array_size, 1)), 0) AS total FROM jobs_preallocations WHERE username = ? AND job_uuid != ?"
    else
        sql = "SELECT COALESCE(SUM(preallocated_gpu_seconds * COALESCE(array_size, 1)), 0) AS total FROM jobs_preallocations WHERE username = ?"
    end
    local stmt, perr = conn:prepare(sql)
    if not stmt then
        return 0
    end
    local ok
    if exclude_job_uuid and exclude_job_uuid ~= "" then
        ok = execute_with_retry(stmt, q_username, exclude_job_uuid)
    else
        ok = execute_with_retry(stmt, q_username)
    end
    if not ok then
        stmt:close()
        return 0
    end
    local row = stmt:fetch(true)
    stmt:close()
    return (row and row.total) or 0
end

-- Compute current preallocated GPU seconds for a given account
local function get_current_preallocated_gpu_for_account(conn, q_account, exclude_job_uuid)
    local sql
    if exclude_job_uuid and exclude_job_uuid ~= "" then
        sql = "SELECT COALESCE(SUM(preallocated_gpu_seconds * COALESCE(array_size, 1)), 0) AS total FROM jobs_preallocations WHERE account = ? AND job_uuid != ?"
    else
        sql = "SELECT COALESCE(SUM(preallocated_gpu_seconds * COALESCE(array_size, 1)), 0) AS total FROM jobs_preallocations WHERE account = ?"
    end
    local stmt, perr = conn:prepare(sql)
    if not stmt then
        return 0
    end
    local ok
    if exclude_job_uuid and exclude_job_uuid ~= "" then
        ok = execute_with_retry(stmt, q_account, exclude_job_uuid)
    else
        ok = execute_with_retry(stmt, q_account)
    end
    if not ok then
        stmt:close()
        return 0
    end
    local row = stmt:fetch(true)
    stmt:close()
    return (row and row.total) or 0
end

-- Main job submit function
function slurm_job_submit(job_desc, part_list, submit_uid)
    local username = job_desc.user_name
    local account = resolve_account(job_desc)
    if not account then
        slurm.log_info(
            "slurm_job_submit: reject job submission for user %s: no account or default_account provided",
            username
        )
        slurm.log_user("job submission rejected: no account/default_account set")
        return slurm.ESLURM_INVALID_ACCOUNT
    end
    local num_tasks = normalize_num_tasks(job_desc.num_tasks)
    local num_nodes = normalize_num_nodes(job_desc.max_nodes)
    -- Time limit (in minutes), nil when unlimited
    local time_limit = resolve_time_limit(job_desc.time_limit, job_desc.partition, part_list)
    local array_job_count = count_array_jobs(job_desc.array_inx)
    if not array_job_count then
        slurm.log_user("job submission rejected: invalid array index range (start must be <= end)")
        return slurm.ESLURM_INVALID_ARRAY
    end

    -- Generate UUID for this job. Slurm job_submit callback doesn't provide a job ID
    -- because it is not allocated yet by the scheduler at this point. So we need to
    -- generate a UUID for the job in order to track its preallocation in the database.
    local job_uuid = generate_uuid()

    -- Set the admin_comment with the job UUID
    job_desc.admin_comment = job_uuid

    -- If database doesn't exist, allow the job
    if not database_exists() then
        slurm.log_info("slurm_job_submit: database does not exist, allowing job submission")
        return slurm.SUCCESS
    end

    -- Open a single DB connection for this submit
    local conn, err = connect_db()
    if not conn then
        slurm.log_error("slurm_job_submit: failed to open database: " .. tostring(err))
        return slurm.SUCCESS -- Allow job if DB unavailable
    end

    -- Reject jobs with invalid accounts before recording anything in database
    if CHECK_ASSOCIATIONS and association_exists(conn, username, account) == false then
        conn:close()
        slurm.log_info(
            "slurm_job_submit: reject job submission for user %s: invalid account %s",
            username, tostring(account)
        )
        slurm.log_user("job submission rejected: invalid account %s for user %s", tostring(account), username)
        return slurm.ESLURM_INVALID_ACCOUNT
    end

    local user_data = get_or_create_user(conn, username)
    if not user_data then
        conn:close()
        slurm.log_error("slurm_job_submit: failed to get or create user data for %s", username)
        return slurm.SUCCESS -- Allow job if DB operation fails
    end

    local account_data = get_or_create_account(conn, account)
    if not account_data then
        conn:close()
        slurm.log_error("slurm_job_submit: failed to get or create account data for %s", tostring(account))
        return slurm.SUCCESS
    end

    -- Parse GPU requirements and calculate GPU preallocation
    local gpu_factors = load_gpu_factors(conn)
    if not gpu_factors then
        conn:close()
        slurm.log_error("slurm_job_submit: failed to load GPU factors, allowing job submission")
        return slurm.SUCCESS -- Allow job if DB operation fails
    end
    local gpu_counts, gpu_parse_error = parse_tres_fields(job_desc, num_tasks, num_nodes)
    if not gpu_counts then
        conn:close()
        slurm.log_info("slurm_job_submit: reject job submission for user %s: %s", username, gpu_parse_error or "failed to parse GPU requirements")
        slurm.log_user("job submission rejected: %s", gpu_parse_error or "failed to parse GPU requirements")
        return slurm.ESLURM_INVALID_GRES
    end

    -- Jobs with unlimited time limit cannot fit in limited quotas. They are
    -- accepted without preallocation when all their quotas are unlimited.
    if not time_limit then
        local quota = limited_quota(user_data, account_data, next(gpu_counts) ~= nil)
        if quota then
            conn:close()
            slurm.log_info(
                "slurm_job_submit: reject user %s (account=%s) job submission due to %s quota: unlimited time limit",
                username, tostring(account), quota
            )
            slurm.log_user("job submission rejected due to %s quota: time limit is unlimited, set a time limit with --time", quota)
            return slurm.ESLURM_ACCESS_DENIED
        end
    end

    -- Calculate requested CPU seconds: tasks * time_limit (in minutes)
    local requested_cpu_seconds = num_tasks * (time_limit or 0) * 60
    local available_user = -1
    local available_account = -1
    local requested_gpu_seconds = calculate_gpu_seconds(gpu_counts, time_limit or 0, gpu_factors)
    local available_user_gpu = -1
    local available_account_gpu = -1

    -- If quota limited, verify and log remaining
    if user_data.quota ~= -1 then
        local available_user_s = available_seconds(user_data.quota, user_data.consumed_s, get_current_preallocated_for_user(conn, username, nil))
        available_user = math.floor(available_user_s / 60)
        if (requested_cpu_seconds * array_job_count) > available_user_s then
            conn:close()
            slurm.log_info(
                "slurm_job_submit: reject user %s job submission due to quota: requested_cpu_minutes=%d available_quota=%d",
                username, ceil_minutes(requested_cpu_seconds * array_job_count), available_user
            )
            slurm.log_user("job submission rejected due to user quota (requested: %d CPU mins, available: %d CPU mins)", ceil_minutes(requested_cpu_seconds * array_job_count), available_user)
            return slurm.ESLURM_ACCESS_DENIED
        end
    end

    -- If account has a quota, enforce it too
    if account_data.quota ~= -1 then
        local available_account_s = available_seconds(account_data.quota, account_data.consumed_s, get_current_preallocated_for_account(conn, account, nil))
        available_account = math.floor(available_account_s / 60)
        if (requested_cpu_seconds * array_job_count) > available_account_s then
            conn:close()
            slurm.log_info(
                "slurm_job_submit: reject account %s job submission due to quota: requested_cpu_minutes=%d available_quota=%d",
                tostring(account), ceil_minutes(requested_cpu_seconds * array_job_count), available_account
            )
            slurm.log_user("job submission rejected due to account quota (requested: %d CPU mins, available: %d CPU mins)", ceil_minutes(requested_cpu_seconds * array_job_count), available_account)
            return slurm.ESLURM_ACCESS_DENIED
        end
    end

    -- Check GPU quota for user
    if user_data.quota_gpu ~= -1 then
        local available_user_gpu_s = available_seconds(user_data.quota_gpu, user_data.consumed_gpu_s, get_current_preallocated_gpu_for_user(conn, username, nil))
        available_user_gpu = math.floor(available_user_gpu_s / 60)
        if (requested_gpu_seconds * array_job_count) > available_user_gpu_s then
            conn:close()
            slurm.log_info(
                "slurm_job_submit: reject user %s job submission due to GPU quota: requested_gpu_minutes=%d available_quota=%d",
                username, ceil_minutes(requested_gpu_seconds * array_job_count), available_user_gpu
            )
            slurm.log_user("job submission rejected due to user GPU quota (requested: %d GPU mins, available: %d GPU mins)", ceil_minutes(requested_gpu_seconds * array_job_count), available_user_gpu)
            return slurm.ESLURM_ACCESS_DENIED
        end
    end

    -- Check GPU quota for account
    if account_data.quota_gpu ~= -1 then
        local available_account_gpu_s = available_seconds(account_data.quota_gpu, account_data.consumed_gpu_s, get_current_preallocated_gpu_for_account(conn, account, nil))
        available_account_gpu = math.floor(available_account_gpu_s / 60)
        if (requested_gpu_seconds * array_job_count) > available_account_gpu_s then
            conn:close()
            slurm.log_info(
                "slurm_job_submit: reject account %s job submission due to GPU quota: requested_gpu_minutes=%d available_quota=%d",
                tostring(account), ceil_minutes(requested_gpu_seconds * array_job_count), available_account_gpu
            )
            slurm.log_user("job submission rejected due to account GPU quota (requested: %d GPU mins, available: %d GPU mins)", ceil_minutes(requested_gpu_seconds * array_job_count), available_account_gpu)
            return slurm.ESLURM_ACCESS_DENIED
        end
    end

    -- Create job record with preallocated CPU and GPU seconds
    local created = create_job_record(conn, job_uuid, username, account or "", requested_cpu_seconds, requested_gpu_seconds, array_job_count)
    if created and conn.commit then
        pcall(function() conn:commit() end)
    end
    conn:close()
    if not created then
        slurm.log_error("slurm_job_submit: failed to create job record, but allowing job")
        -- Don't reject the job if we can't update the database
    end

    -- Single informative allow log (tasks, time, requested, remaining quota considering preallocations)
    slurm.log_info(
        "slurm_job_submit: allow user %s (account=%s) job submission: tasks=%d time_limit=%s array_jobs=%d requested_cpu_minutes=%d user_avail=%d account_avail=%d requested_gpu_minutes=%d user_gpu_avail=%d account_gpu_avail=%d",
        username, tostring(account or ""), num_tasks, tostring(time_limit or "unlimited"), array_job_count, ceil_minutes(requested_cpu_seconds), available_user, available_account, ceil_minutes(requested_gpu_seconds), available_user_gpu, available_account_gpu
    )

    return slurm.SUCCESS
end

-- Job modify function (for job updates)
function slurm_job_modify(job_desc, job_record, part_list, modify_uid)
    -- The job description only contains the modified fields, the other fields
    -- are taken from the job record.
    local username = job_desc.user_name or job_record.user_name
    local account = job_desc.account
    if not account or account == "" then
        account = job_record.account
    end
    if not account or account == "" then
        account = resolve_account(job_desc)
    end
    if not account then
        slurm.log_info(
                "slurm_job_modify: reject job modification for user %s: no account or default_account provided",
                username
            )
        slurm.log_user("job modification rejected: no account/default_account set")
        return slurm.ESLURM_INVALID_ACCOUNT
    end
    local array_job_count = nil
    if job_desc.array_inx and job_desc.array_inx ~= "" then
        array_job_count = count_array_jobs(job_desc.array_inx)
        if not array_job_count then
            slurm.log_user("job modification rejected: invalid array index range (start must be <= end)")
            return slurm.ESLURM_INVALID_ARRAY
        end
    end

    -- If database doesn't exist, allow the modification
    if not database_exists() then
        slurm.log_info("slurm_job_modify: database does not exist, allowing job modification")
        return slurm.SUCCESS
    end

    -- Open a single DB connection for this modify
    local conn, err = connect_db()
    if not conn then
        slurm.log_info("slurm_job_modify: database connection failed, allowing job modification")
        return slurm.SUCCESS
    end

    -- Reject modifications to invalid accounts before recording anything in
    -- database. The account is checked only when modified.
    if CHECK_ASSOCIATIONS and job_desc.account and job_desc.account ~= "" and username
        and association_exists(conn, username, job_desc.account) == false then
        conn:close()
        slurm.log_info(
            "slurm_job_modify: reject job modification for user %s: invalid account %s",
            username, tostring(job_desc.account)
        )
        slurm.log_user("job modification rejected: invalid account %s for user %s", tostring(job_desc.account), username)
        return slurm.ESLURM_INVALID_ACCOUNT
    end

    local user_data = get_or_create_user(conn, username)
    if not user_data then
        conn:close()
        slurm.log_info("slurm_job_modify: failed to get user data, allowing job modification")
        return slurm.SUCCESS
    end

    local account_data = get_or_create_account(conn, account)
    if not account_data then
        conn:close()
        slurm.log_info("slurm_job_modify: failed to get account data, allowing job modification")
        return slurm.SUCCESS
    end

    -- Get the job UUID from admin_comment
    local job_uuid = job_record.admin_comment
    if not job_uuid or job_uuid == "" then
        conn:close()
        slurm.log_info("slurm_job_modify: no job UUID found in admin_comment, allowing job modification")
        return slurm.SUCCESS
    end

    -- Get the current preallocation of the job
    local preallocation = get_job_preallocation(conn, job_uuid)
    if not preallocation then
        conn:close()
        slurm.log_info("slurm_job_modify: no preallocation found for job %s, allowing job modification", job_uuid)
        return slurm.SUCCESS
    end

    -- Partition, from job description if modified or job record
    local partition = job_desc.partition
    if not partition or partition == "" then
        partition = job_record.partition
    end

    -- Current time limit (in minutes) of the job, nil when unlimited
    local current_time_limit = resolve_time_limit(job_record.time_limit, job_record.partition, part_list)

    -- Time limit (in minutes), from job description if modified or job record,
    -- nil when unlimited
    local time_limit = job_desc.time_limit
    if is_unset(time_limit) then
        time_limit = job_record.time_limit
    end
    time_limit = resolve_time_limit(time_limit, partition, part_list)

    -- Number of tasks, from job description if modified. Otherwise, the number
    -- of tasks is not available in job record, it is derived from the current
    -- CPU preallocation and the current time limit of the job.
    local num_tasks
    if not is_unset(job_desc.num_tasks) then
        num_tasks = normalize_num_tasks(job_desc.num_tasks)
    elseif preallocation.cpu_seconds > 0 and preallocation.cpu_seconds % (NO_VAL * 60) == 0 then
        -- Preallocation recorded by previous versions of this script for jobs
        -- submitted without time limit, computed with NO_VAL time limit.
        num_tasks = math.floor(preallocation.cpu_seconds / (NO_VAL * 60))
    elseif not current_time_limit or current_time_limit <= 0 then
        num_tasks = 1
    else
        num_tasks = math.max(1, math.floor(preallocation.cpu_seconds / (current_time_limit * 60) + 0.5))
    end

    -- Number of nodes, from job description if modified or job record
    local num_nodes
    if not is_unset(job_desc.max_nodes) then
        num_nodes = normalize_num_nodes(job_desc.max_nodes)
    else
        num_nodes = normalize_num_nodes(job_record.max_nodes)
    end

    -- Number of array jobs, from job description if modified or current
    -- preallocation (array index is not available in job record)
    if not array_job_count then
        array_job_count = preallocation.array_size
    end

    -- TRES fields, from job description if modified or job record
    local tres = {}
    for _, field in ipairs({"tres_per_job", "tres_per_task", "tres_per_node", "tres_per_socket"}) do
        if job_desc[field] ~= nil then
            tres[field] = job_desc[field]
        else
            tres[field] = job_record[field]
        end
    end

    -- Parse GPU requirements and calculate GPU preallocation
    local gpu_factors = load_gpu_factors(conn)
    if not gpu_factors then
        conn:close()
        slurm.log_error("slurm_job_modify: failed to load GPU factors, allowing job modification")
        return slurm.SUCCESS
    end
    local gpu_counts, gpu_parse_error = parse_tres_fields(tres, num_tasks, num_nodes)
    if not gpu_counts then
        conn:close()
        slurm.log_info("slurm_job_modify: reject job modification for user %s: %s", username, gpu_parse_error or "failed to parse GPU requirements")
        slurm.log_user("job modification rejected: %s", gpu_parse_error or "failed to parse GPU requirements")
        return slurm.ESLURM_INVALID_GRES
    end

    -- Jobs with unlimited time limit cannot fit in limited quotas. They are
    -- accepted without preallocation when all their quotas are unlimited.
    if not time_limit then
        local quota = limited_quota(user_data, account_data, next(gpu_counts) ~= nil)
        if quota then
            conn:close()
            slurm.log_info(
                "slurm_job_modify: reject user %s (account=%s) job modification due to %s quota: unlimited time limit",
                username, tostring(account), quota
            )
            slurm.log_user("job modification rejected due to %s quota: time limit is unlimited, set a time limit", quota)
            return slurm.ESLURM_ACCESS_DENIED
        end
    end

    -- Calculate requested CPU seconds: tasks * time_limit (in minutes)
    local requested_cpu_seconds = num_tasks * (time_limit or 0) * 60
    local available_user = -1
    local available_account = -1
    local requested_gpu_seconds = calculate_gpu_seconds(gpu_counts, time_limit or 0, gpu_factors)
    local available_user_gpu = -1
    local available_account_gpu = -1

    -- Check quota
    if user_data.quota ~= -1 then
        local available_user_s = available_seconds(user_data.quota, user_data.consumed_s, get_current_preallocated_for_user(conn, username, job_uuid))
        available_user = math.floor(available_user_s / 60)
        if (requested_cpu_seconds * array_job_count) > available_user_s then
            conn:close()
            slurm.log_info(
                "slurm_job_modify: reject user %s job modification due to quota: requested_cpu_minutes=%d available_quota=%d",
                username, ceil_minutes(requested_cpu_seconds * array_job_count), available_user
            )
            slurm.log_user("job modification rejected due to user quota (requested: %d CPU mins, available: %d CPU mins)", ceil_minutes(requested_cpu_seconds * array_job_count), available_user)
            return slurm.ESLURM_ACCESS_DENIED
        end
    end

    if account_data and account_data.quota ~= -1 then
        local available_account_s = available_seconds(account_data.quota, account_data.consumed_s, get_current_preallocated_for_account(conn, account, job_uuid))
        available_account = math.floor(available_account_s / 60)
        if (requested_cpu_seconds * array_job_count) > available_account_s then
            conn:close()
            slurm.log_info(
                "slurm_job_modify: reject account %s job modification due to quota: requested_cpu_minutes=%d available_quota=%d",
                tostring(account), ceil_minutes(requested_cpu_seconds * array_job_count), available_account
            )
            slurm.log_user("job modification rejected due to account quota (requested: %d CPU mins, available: %d CPU mins)", ceil_minutes(requested_cpu_seconds * array_job_count), available_account)
            return slurm.ESLURM_ACCESS_DENIED
        end
    end

    -- Check GPU quota for user
    if user_data.quota_gpu ~= -1 then
        local available_user_gpu_s = available_seconds(user_data.quota_gpu, user_data.consumed_gpu_s, get_current_preallocated_gpu_for_user(conn, username, job_uuid))
        available_user_gpu = math.floor(available_user_gpu_s / 60)
        if (requested_gpu_seconds * array_job_count) > available_user_gpu_s then
            conn:close()
            slurm.log_info(
                "slurm_job_modify: reject user %s job modification due to GPU quota: requested_gpu_minutes=%d available_quota=%d",
                username, ceil_minutes(requested_gpu_seconds * array_job_count), available_user_gpu
            )
            slurm.log_user("job modification rejected due to user GPU quota (requested: %d GPU mins, available: %d GPU mins)", ceil_minutes(requested_gpu_seconds * array_job_count), available_user_gpu)
            return slurm.ESLURM_ACCESS_DENIED
        end
    end

    -- Check GPU quota for account
    if account_data and account_data.quota_gpu ~= -1 then
        local available_account_gpu_s = available_seconds(account_data.quota_gpu, account_data.consumed_gpu_s, get_current_preallocated_gpu_for_account(conn, account, job_uuid))
        available_account_gpu = math.floor(available_account_gpu_s / 60)
        if (requested_gpu_seconds * array_job_count) > available_account_gpu_s then
            conn:close()
            slurm.log_info(
                "slurm_job_modify: reject account %s job modification due to GPU quota: requested_gpu_minutes=%d available_quota=%d",
                tostring(account), ceil_minutes(requested_gpu_seconds * array_job_count), available_account_gpu
            )
            slurm.log_user("job modification rejected due to account GPU quota (requested: %d GPU mins, available: %d GPU mins)", ceil_minutes(requested_gpu_seconds * array_job_count), available_account_gpu)
            return slurm.ESLURM_ACCESS_DENIED
        end
    end

    -- Update the job record with new preallocated CPU and GPU seconds (no commit here)
    local updated = update_job_preallocation(conn, job_uuid, account, requested_cpu_seconds, requested_gpu_seconds, array_job_count)
    if updated and conn.commit then
        pcall(function() conn:commit() end)
    end

    conn:close()

    -- Informative allow log for modification
    slurm.log_info(
        "slurm_job_modify: allow user %s (account=%s) job modification: tasks=%d time_limit=%s array_jobs=%d requested_cpu_minutes=%d user_avail=%d account_avail=%d requested_gpu_minutes=%d user_gpu_avail=%d account_gpu_avail=%d",
        username, tostring(account or ""), num_tasks, tostring(time_limit or "unlimited"), array_job_count, ceil_minutes(requested_cpu_seconds), available_user, available_account, ceil_minutes(requested_gpu_seconds), available_user_gpu, available_account_gpu
    )

    return slurm.SUCCESS
end

slurm.log_info("slurm_job_submit: initialized")
return slurm.SUCCESS
