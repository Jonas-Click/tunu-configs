#include "vesc_c_if.h"
#include "boost_core.h"

#include <math.h>
#include <string.h>

HEADER
_Static_assert(SYSTEM_TICK_RATE_HZ == 10000, "boost guard requires the reviewed VESC clock ABI");

typedef struct {
    struct boost_core core;
    lib_mutex mutex;
    lib_thread thread;
    uint32_t ticks;
    uint32_t milliseconds;
    uint32_t tick_remainder;
    uint32_t reported_at;
    bool registered;
    bool write_failed;
} guard_data;

static guard_data *context(void) {
    void **slot = VESC_IF->get_arg(PROG_ADDR);
    return slot ? *slot : 0;
}

static uint32_t clock_ms(guard_data *data) {
    uint32_t ticks = VESC_IF->system_time_ticks();
    uint32_t elapsed = ticks - data->ticks;
    data->ticks = ticks;
    data->milliseconds += elapsed / 10u;
    data->tick_remainder += elapsed % 10u;
    data->milliseconds += data->tick_remainder / 10u;
    data->tick_remainder %= 10u;
    return data->milliseconds;
}

static void inhibit_output(void) {
    /* A failed restoration must not leave the ADC app using an elevated cap. */
    unsigned char packet[] = {63, 0, 255, 255, 255, 255};
    VESC_IF->commands_process_packet(packet, sizeof(packet), 0);
}

static bool assign_current(float amps) {
    // The SDK setter uses a shared config pool even for this single motor limit.
    VESC_IF->sys_lock();
    bool result = VESC_IF->set_cfg_float(CFG_PARAM_l_in_current_max, amps);
    VESC_IF->sys_unlock();
    return result;
}

static bool apply_current(guard_data *data) {
    float wanted = data->core.output_amps;
    float current = VESC_IF->get_cfg_float(CFG_PARAM_l_in_current_max);
    if (isfinite(current) && fabsf(current - wanted) < 0.05f) {
        return true;
    }
    bool result = assign_current(wanted);
    current = VESC_IF->get_cfg_float(CFG_PARAM_l_in_current_max);
    if (!result || !isfinite(current) || fabsf(current - wanted) >= 0.05f) {
        boost_abort(&data->core);
        data->core.reason = BOOST_REASON_WRITE;
        assign_current(data->core.normal_amps);
        current = VESC_IF->get_cfg_float(CFG_PARAM_l_in_current_max);
        if (!isfinite(current) || fabsf(current - data->core.normal_amps) >= 0.05f) {
            inhibit_output();
        }
        data->write_failed = true;
        return false;
    }
    return true;
}

static void evaluate(guard_data *data) {
    bool safe = !data->write_failed && VESC_IF->mc_get_fault() == FAULT_CODE_NONE
        && !VESC_IF->app_is_output_disabled() && VESC_IF->mc_get_rpm() >= -50.0f;
    boost_step(&data->core, clock_ms(data), safe);
    if (data->write_failed) {
        data->core.reason = BOOST_REASON_WRITE;
    }
    if (data->core.configured) {
        apply_current(data);
    }
}

static void report(guard_data *data) {
    uint8_t packet[8];
    VESC_IF->mutex_lock(data->mutex);
    boost_status(&data->core, packet);
    data->reported_at = data->milliseconds;
    VESC_IF->mutex_unlock(data->mutex);
    VESC_IF->can_transmit_sid(0x7e3, packet, sizeof(packet));
}

static bool number(lbm_value value, float *result) {
    if (!VESC_IF->lbm_is_number(value)) {
        return false;
    }
    *result = VESC_IF->lbm_dec_as_float(value);
    return isfinite(*result);
}

static lbm_value boolean(bool value) {
    return value ? VESC_IF->lbm_enc_sym_true : VESC_IF->lbm_enc_sym_nil;
}

static lbm_value api(lbm_value *args, lbm_uint count) {
    (void)args;
    guard_data *data = context();
    return VESC_IF->lbm_enc_i(count == 0 && data && data->registered && data->thread ? 1 : 0);
}

static lbm_value configure(lbm_value *args, lbm_uint count) {
    guard_data *data = context();
    float values[6];
    if (count != 6 || !data || !data->registered || !data->thread) {
        return boolean(false);
    }
    for (unsigned i = 0; i < 6; ++i) {
        if (!number(args[i], &values[i])) {
            return boolean(false);
        }
    }
    if (values[1] < 0.1f || values[1] > 20.0f || values[2] < 1.0f || values[2] > 3600.0f
            || values[3] < 20 || values[3] > 500 || values[4] < 100 || values[4] > 5000
            || values[5] < 20 || values[5] > 1000) {
        return boolean(false);
    }
    struct boost_config config = {
        .per_pack_amps = values[0], .duration_ms = (uint32_t)(values[1] * 1000.0f),
        .cooldown_ms = (uint32_t)(values[2] * 1000.0f),
        .heartbeat_ms = (uint32_t)values[3], .supply_ms = (uint32_t)values[4],
        .release_ms = (uint32_t)values[5]
    };
    VESC_IF->mutex_lock(data->mutex);
    bool result = false;
    if (!data->core.configured) {
        result = boost_init(&data->core, &config,
                            VESC_IF->get_cfg_float(CFG_PARAM_l_in_current_max), clock_ms(data));
    }
    VESC_IF->mutex_unlock(data->mutex);
    return boolean(result);
}

static lbm_value heartbeat(lbm_value *args, lbm_uint count) {
    guard_data *data = context();
    float control, drive;
    if (!data || count != 2 || !number(args[0], &control) || !number(args[1], &drive)
            || control < 0 || control > 7 || floorf(control) != control || (drive != 0 && drive != 1)) {
        return boolean(false);
    }
    VESC_IF->mutex_lock(data->mutex);
    evaluate(data);
    boost_heartbeat(&data->core, (uint8_t)control, drive != 0, data->milliseconds);
    evaluate(data);
    bool due = data->core.configured && data->milliseconds - data->reported_at >= 200;
    VESC_IF->mutex_unlock(data->mutex);
    if (due) {
        report(data);
    }
    return boolean(true);
}

static lbm_value supply(lbm_value *args, lbm_uint count) {
    guard_data *data = context();
    float packs, normal;
    if (!data || count != 2 || !number(args[0], &packs) || !number(args[1], &normal)
            || packs < 0 || packs > 3 || floorf(packs) != packs) {
        return boolean(false);
    }
    VESC_IF->mutex_lock(data->mutex);
    evaluate(data);
    bool result = data->core.configured
        && boost_supply(&data->core, (uint8_t)packs, normal, data->milliseconds);
    evaluate(data);
    VESC_IF->mutex_unlock(data->mutex);
    return boolean(result);
}

static lbm_value normal(lbm_value *args, lbm_uint count) {
    guard_data *data = context();
    float amps, packs;
    if (!data || count != 2 || !number(args[0], &amps) || !number(args[1], &packs)
            || packs < 0 || packs > 3 || floorf(packs) != packs) {
        return boolean(false);
    }
    VESC_IF->mutex_lock(data->mutex);
    evaluate(data);
    bool result = data->core.configured && boost_normal(&data->core, amps, (uint8_t)packs);
    evaluate(data);
    VESC_IF->mutex_unlock(data->mutex);
    return boolean(result);
}

static lbm_value get_normal(lbm_value *args, lbm_uint count) {
    (void)args;
    guard_data *data = context();
    if (!data || count != 0) {
        return VESC_IF->lbm_enc_sym_eerror;
    }
    VESC_IF->mutex_lock(data->mutex);
    float amps = data->core.configured ? data->core.normal_amps
        : VESC_IF->get_cfg_float(CFG_PARAM_l_in_current_max);
    VESC_IF->mutex_unlock(data->mutex);
    return VESC_IF->lbm_enc_float(amps);
}

static lbm_value send_report(lbm_value *args, lbm_uint count) {
    (void)args;
    guard_data *data = context();
    if (!data || count != 0) {
        return boolean(false);
    }
    VESC_IF->mutex_lock(data->mutex);
    evaluate(data);
    bool configured = data->core.configured;
    VESC_IF->mutex_unlock(data->mutex);
    if (configured) {
        report(data);
    }
    return boolean(configured);
}

static void worker(void *argument) {
    guard_data *data = argument;
    if (VESC_IF->thread_set_priority) {
        VESC_IF->thread_set_priority(2);
    }
    while (!VESC_IF->should_terminate()) {
        VESC_IF->mutex_lock(data->mutex);
        evaluate(data);
        VESC_IF->mutex_unlock(data->mutex);
        VESC_IF->sleep_ms(5);
    }
}

static void stop(void *argument) {
    guard_data *data = argument;
    if (data->thread) {
        VESC_IF->request_terminate(data->thread);
    }
    VESC_IF->mutex_lock(data->mutex);
    bool configured = data->core.configured;
    if (configured) {
        boost_abort(&data->core);
        apply_current(data);
    }
    VESC_IF->mutex_unlock(data->mutex);
    if (configured) {
        report(data);
    }
    void **slot = VESC_IF->get_arg(PROG_ADDR);
    if (slot) {
        *slot = 0;
    }
    VESC_IF->free(data->mutex);
    VESC_IF->free(data);
}

INIT_FUN(lib_info *info) {
    INIT_START
    if (!VESC_IF->get_arg || !VESC_IF->sys_lock || !VESC_IF->sys_unlock
            || !VESC_IF->system_time_ticks || !VESC_IF->spawn || !VESC_IF->mutex_create
            || !VESC_IF->mutex_lock || !VESC_IF->mutex_unlock || !VESC_IF->request_terminate
            || !VESC_IF->get_cfg_float || !VESC_IF->set_cfg_float || !VESC_IF->mc_get_fault
            || !VESC_IF->app_is_output_disabled || !VESC_IF->mc_get_rpm
            || !VESC_IF->commands_process_packet || !VESC_IF->can_transmit_sid) {
        return false;
    }
    guard_data *data = VESC_IF->malloc(sizeof(*data));
    if (!data) {
        return false;
    }
    memset(data, 0, sizeof(*data));
    data->mutex = VESC_IF->mutex_create();
    if (!data->mutex) {
        VESC_IF->free(data);
        return false;
    }
    data->ticks = VESC_IF->system_time_ticks();
    info->arg = data;
    info->stop_fun = stop;
    data->registered = VESC_IF->lbm_add_extension("ext-boost-api", api)
        & VESC_IF->lbm_add_extension("ext-boost-config", configure)
        & VESC_IF->lbm_add_extension("ext-boost-heartbeat", heartbeat)
        & VESC_IF->lbm_add_extension("ext-boost-supply", supply)
        & VESC_IF->lbm_add_extension("ext-boost-normal", normal)
        & VESC_IF->lbm_add_extension("ext-boost-normal-current", get_normal)
        & VESC_IF->lbm_add_extension("ext-boost-report", send_report);
    if (data->registered) {
        data->thread = VESC_IF->spawn(worker, 1024, "BoostGuard", data);
    }
    return true;
}
