#ifndef TUNU_BOOST_TEST_API_H
#define TUNU_BOOST_TEST_API_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef double lbm_value;
typedef uint32_t lbm_uint;
typedef int32_t lbm_int;
typedef void *lib_thread;
typedef void *lib_mutex;
typedef int mc_fault_code;
typedef int CFG_PARAM;
#define CFG_PARAM_l_in_current_max 2
#define FAULT_CODE_NONE 0
#define SYSTEM_TICK_RATE_HZ 10000

typedef struct {
    void (*stop_fun)(void *);
    void *arg;
    uint32_t base_addr;
} lib_info;

typedef struct {
    uint32_t (*system_time_ticks)(void);
    void **(*get_arg)(uint32_t);
    void (*sys_lock)(void);
    void (*sys_unlock)(void);
    lib_thread (*spawn)(void (*)(void *), size_t, const char *, void *);
    lib_mutex (*mutex_create)(void);
    void (*mutex_lock)(lib_mutex);
    void (*mutex_unlock)(lib_mutex);
    void (*request_terminate)(lib_thread);
    bool (*should_terminate)(void);
    float (*get_cfg_float)(CFG_PARAM);
    bool (*set_cfg_float)(CFG_PARAM, float);
    mc_fault_code (*mc_get_fault)(void);
    bool (*app_is_output_disabled)(void);
    float (*mc_get_rpm)(void);
    void (*commands_process_packet)(unsigned char *, unsigned int, void (*)(unsigned char *, unsigned int));
    void (*can_transmit_sid)(uint32_t, const uint8_t *, uint8_t);
    void *(*malloc)(size_t);
    void (*free)(void *);
    bool (*lbm_add_extension)(char *, lbm_value (*)(lbm_value *, lbm_uint));
    bool (*lbm_is_number)(lbm_value);
    float (*lbm_dec_as_float)(lbm_value);
    lbm_value (*lbm_enc_i)(lbm_int);
    lbm_value (*lbm_enc_float)(float);
    lbm_value lbm_enc_sym_true;
    lbm_value lbm_enc_sym_nil;
    lbm_value lbm_enc_sym_eerror;
    void (*thread_set_priority)(int);
    void (*sleep_ms)(uint32_t);
} boost_test_api;
extern boost_test_api boost_test_interface;
extern void *boost_test_arg;
#define HEADER
#define INIT_FUN bool init
#define INIT_START ((void)0);
#define VESC_IF (&boost_test_interface)
#define ARG boost_test_arg
#define PROG_ADDR 0
#endif
