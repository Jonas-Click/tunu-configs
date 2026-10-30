#include "vesc_c_if.h"
#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

boost_test_api boost_test_interface;
void *boost_test_arg;
#include "../tools/boost/boost_guard.c"

static uint32_t ticks;
static float amps;
static float hardware_ceiling;
static bool output_disabled;
static bool fail_write;
static bool system_locked;
static bool core_locked;
static bool simulate_can_stall;
static unsigned worker_iterations;
static int fault;
static float rpm;
static bool terminated;
static uint8_t last_status[8];
static unsigned reports;
static struct {
    char name[40];
    lbm_value (*function)(lbm_value *, lbm_uint);
} extensions[7];
static unsigned extension_count;

static uint32_t get_ticks(void) { return ticks; }
static void **get_arg(uint32_t address) { (void)address; return &boost_test_arg; }
static void sys_lock(void) { assert(!system_locked); system_locked = true; }
static void sys_unlock(void) { assert(system_locked); system_locked = false; }
static void *spawn_stub(void (*function)(void *), size_t bytes, const char *name, void *arg) {
    assert(function && bytes >= 1024 && name && arg);
    return (void *)1;
}
static void *mutex_stub(void) { return malloc(1); }
static void mutex_lock_stub(void *mutex) { assert(mutex && !core_locked); core_locked = true; }
static void mutex_unlock_stub(void *mutex) { assert(mutex && core_locked); core_locked = false; }
static void terminate_stub(void *thread) { assert(thread); terminated = true; }
static bool done(void) {
    if (worker_iterations) { --worker_iterations; return false; }
    return true;
}
static float get_current(CFG_PARAM parameter) { assert(parameter == 2); return amps; }
static bool set_current(CFG_PARAM parameter, float value) {
    assert(system_locked);
    assert(parameter == 2);
    if (fail_write) { return false; }
    amps = fminf(value, hardware_ceiling);
    return true;
}
static mc_fault_code get_fault(void) { return fault; }
static bool is_disabled(void) { return output_disabled; }
static float get_rpm(void) { return rpm; }
static void command(unsigned char *packet, unsigned int bytes, void (*reply)(unsigned char *, unsigned int)) {
    (void)reply;
    assert(bytes == 6 && packet[0] == 63 && packet[1] == 0);
    for (unsigned i = 2; i < 6; ++i) { assert(packet[i] == 255); }
    output_disabled = true;
}
static void send_status(uint32_t id, const uint8_t *packet, uint8_t bytes) {
    assert(!core_locked && !system_locked);
    assert(id == 0x7e3 && bytes == 8);
    if (simulate_can_stall) {
        worker_iterations = 60;
        worker(boost_test_arg);
        assert(amps == 60 && !((guard_data *)boost_test_arg)->core.active);
    }
    memcpy(last_status, packet, 8);
    ++reports;
}
static bool add_extension(char *name, lbm_value (*function)(lbm_value *, lbm_uint)) {
    assert(extension_count < 7);
    strcpy(extensions[extension_count].name, name);
    extensions[extension_count++].function = function;
    return true;
}
static bool numeric(lbm_value value) { return isfinite(value); }
static float decode(lbm_value value) { return value; }
static lbm_value encode_i(lbm_int value) { return value; }
static lbm_value encode_f(float value) { return value; }
static void sleep_stub(uint32_t ms) { ticks += ms * 10; }

static lbm_value call(const char *name, lbm_value *args, lbm_uint count) {
    for (unsigned i = 0; i < extension_count; ++i) {
        if (!strcmp(name, extensions[i].name)) { return extensions[i].function(args, count); }
    }
    assert(false);
    return -1;
}

static lib_info start(void) {
    extension_count = 0;
    ticks = UINT32_MAX - 6000;
    amps = 60;
    hardware_ceiling = 170;
    output_disabled = false;
    fail_write = false;
    terminated = false;
    system_locked = core_locked = simulate_can_stall = false;
    worker_iterations = 0;
    fault = 0;
    rpm = 0;
    reports = 0;
    boost_test_interface = (boost_test_api){
        .system_time_ticks = get_ticks, .get_arg = get_arg, .sys_lock = sys_lock, .sys_unlock = sys_unlock,
        .spawn = spawn_stub, .mutex_create = mutex_stub,
        .mutex_lock = mutex_lock_stub, .mutex_unlock = mutex_unlock_stub, .request_terminate = terminate_stub,
        .should_terminate = done, .get_cfg_float = get_current, .set_cfg_float = set_current,
        .mc_get_fault = get_fault, .app_is_output_disabled = is_disabled, .mc_get_rpm = get_rpm,
        .commands_process_packet = command, .can_transmit_sid = send_status,
        .malloc = malloc, .free = free, .lbm_add_extension = add_extension,
        .lbm_is_number = numeric, .lbm_dec_as_float = decode, .lbm_enc_i = encode_i,
        .lbm_enc_float = encode_f, .lbm_enc_sym_true = 1, .lbm_enc_sym_nil = 0,
        .lbm_enc_sym_eerror = -2, .sleep_ms = sleep_stub
    };
    lib_info info = {0};
    assert(init(&info));
    boost_test_arg = info.arg;
    assert(call("ext-boost-api", 0, 0) == 1);
    lbm_value configuration[] = {85, 1, 1, 250, 1000, 100};
    assert(call("ext-boost-config", configuration, 6) == 1);
    assert(call("ext-boost-config", configuration, 6) == 0);
    return info;
}

static void feed(bool drive) {
    lbm_value heartbeat_args[] = {3, drive};
    lbm_value supply_args[] = {1, 60};
    assert(call("ext-boost-heartbeat", heartbeat_args, 2) == 1);
    assert(call("ext-boost-supply", supply_args, 2) == 1);
}

static void arm_guard(void) {
    feed(false);
    for (unsigned i = 0; i < 50; ++i) {
        ticks += 200;
        feed(false);
    }
    assert(amps == 60);
    feed(true);
    assert(amps == 85);
}

static void heartbeat_expiry(void) {
    lib_info info = start();
    arm_guard();
    ticks += 2500;
    evaluate(info.arg);
    assert(amps == 60);
    assert(!((guard_data *)info.arg)->core.active);
    assert(call("ext-boost-report", 0, 0) == 1);
    assert(reports && last_status[1] == 0xb1 && !(last_status[2] & 16));
    info.stop_fun(info.arg);
    assert(terminated && amps == 60);
}

static void unload_and_fault(void) {
    lib_info info = start();
    arm_guard();
    assert(call("ext-boost-normal-current", 0, 0) == 60);
    info.stop_fun(info.arg);
    assert(terminated && amps == 60 && !output_disabled);
    assert(call("ext-boost-api", 0, 0) == 0);
    assert(call("ext-boost-report", 0, 0) == 0);
    info = start();
    arm_guard();
    fault = 1;
    evaluate(info.arg);
    assert(amps == 60);
    info.stop_fun(info.arg);
    info = start();
    arm_guard();
    rpm = -100;
    evaluate(info.arg);
    assert(amps == 60);
    info.stop_fun(info.arg);
}

static void restoration_failure(void) {
    lib_info info = start();
    arm_guard();
    fail_write = true;
    ticks += 2500;
    evaluate(info.arg);
    assert(output_disabled && ((guard_data *)info.arg)->write_failed);
    info.stop_fun(info.arg);
    assert(terminated && output_disabled);
}

static void external_writer_and_validation(void) {
    lib_info info = start();
    arm_guard();
    amps = 150;
    evaluate(info.arg);
    assert(amps == 85);
    lbm_value invalid[] = {NAN, 0};
    assert(call("ext-boost-heartbeat", invalid, 2) == 0);
    invalid[0] = 4;
    assert(call("ext-boost-supply", invalid, 2) == 0);
    info.stop_fun(info.arg);
    assert(amps == 60);
}

static void failed_grant_keeps_a_verified_normal_cap(void) {
    lib_info info = start();
    feed(false);
    for (unsigned i = 0; i < 50; ++i) {
        ticks += 200;
        feed(false);
    }
    fail_write = true;
    feed(true);
    assert(amps == 60 && !output_disabled);
    assert(((guard_data *)info.arg)->write_failed);
    assert(!((guard_data *)info.arg)->core.active);
    info.stop_fun(info.arg);
}

static void patched_ceiling_allows_two_pack_boost(void) {
    lib_info info = start();
    arm_guard();
    lbm_value two_packs[] = {3, 120};
    assert(call("ext-boost-supply", two_packs, 2) == 1);
    assert(amps == 170 && ((guard_data *)info.arg)->core.active && !output_disabled);
    assert(call("ext-boost-normal-current", 0, 0) == 120);
    info.stop_fun(info.arg);
    assert(amps == 120 && !output_disabled);
}

static void hardware_clamping_restores_normal(void) {
    lib_info info = start();
    hardware_ceiling = 150;
    arm_guard();
    lbm_value two_packs[] = {3, 120};
    assert(call("ext-boost-supply", two_packs, 2) == 1);
    guard_data *data = info.arg;
    assert(amps == 120 && !output_disabled && data->write_failed);
    assert(!data->core.active && data->core.reason == BOOST_REASON_WRITE);
    info.stop_fun(info.arg);
}

static void stalled_telemetry_cannot_delay_expiry(void) {
    lib_info info = start();
    arm_guard();
    unsigned previous_reports = reports;
    simulate_can_stall = true;
    assert(call("ext-boost-report", 0, 0) == 1);
    simulate_can_stall = false;
    assert(reports == previous_reports + 1 && amps == 60);
    info.stop_fun(info.arg);
}

int main(void) {
    stalled_telemetry_cannot_delay_expiry();
    patched_ceiling_allows_two_pack_boost();
    hardware_clamping_restores_normal();
    failed_grant_keeps_a_verified_normal_cap();
    heartbeat_expiry();
    unload_and_fault();
    restoration_failure();
    external_writer_and_validation();
    puts("BOOST_GUARD_PASS");
    return 0;
}
