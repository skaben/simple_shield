import machine
import time
import utime
import ujson
import network
import umqttsimple
import config
import webrepl

hostName = b"Power" + config.cfg["client_id"]

station = network.WLAN(network.STA_IF)
station.active(True)
station.config(dhcp_hostname=hostName)

manage_data_initial = {
    "mqtt_connect": False,
    "powerstate": "OFF",
    "ping_msg": b"",
    "ping_timestamp": 0,
    "ping_millis": 0,
    "send_state": False,
}
manage_data = manage_data_initial

# device mgmt


def reset_out():
    for pin_ctrl in config.pins:
        config.pins[pin_ctrl].value(0)


def run_aux_sequence():
    print("running aux sequence")
    config.pins["FAN_POWER"].value(1)
    config.pins["RELAY_POWER"].value(1)
    time.sleep(2)
    config.pins["RELAY_POWER"].value(0)
    time.sleep(1)
    config.pins["RELAY_POWER"].value(1)
    time.sleep(2)
    config.pins["FAN_POWER"].value(0)
    config.pins["RELAY_POWER"].value(0)
    time.sleep(2)
    config.pins["FAN_POWER"].value(1)
    config.pins["RELAY_POWER"].value(1)
    time.sleep(2)
    config.pins["RELAY_POWER"].value(0)
    time.sleep(2)
    config.pins["KBD_POWER"].value(1)
    time.sleep(5)
    config.pins["FAN_POWER"].value(0)


def run_pwr_sequence():
    print("running pwr sequence")
    config.pins["FAN_POWER"].value(1)
    config.pins["RELAY_POWER"].value(0)
    config.pins["KBD_POWER"].value(1)


def run_off_sequence():
    print("running off sequence")
    config.pins["FAN_POWER"].value(0)
    config.pins["RELAY_POWER"].value(0)
    config.pins["KBD_POWER"].value(0)


def change_state(power_state):
    if manage_data["powerstate"] == power_state:
        return
    manage_data["powerstate"] = power_state

    if power_state == "AUX":
        run_aux_sequence()
    elif power_state == "PWR":
        run_pwr_sequence()
    elif power_state == "OFF":
        run_off_sequence()

    return power_state


# network mgmt


def wifi_init():
    station.active(True)
    station.config(dhcp_hostname=hostName)
    station.connect(config.cfg["wlan_ssid"], config.cfg["wlan_password"])
    while not station.isconnected():
        for x in range(6):
            config.pins["FAN_POWER"].value(1)
            time.sleep(0.25)
            config.pins["FAN_POWER"].value(0)
            time.sleep(0.25)
    print("Connection successful")
    print(station.ifconfig())
    webrepl.start()


def mqtt_init():
    manage_data["mqtt_connect"] = False
    while not manage_data.get("mqtt_connect"):
        restart_and_reconnect()
        client = connect_and_subscribe()
    return client


def connect_and_subscribe():
    addr = str(station.ifconfig()[0]).split(".")
    server = ".".join(addr[:3] + ["254"])

    client = umqttsimple.MQTTClient(
        config.cfg.get("client_id"),
        server,
        config.cfg.get("port"),
        config.cfg.get("user"),
        config.cfg.get("password"),
    )
    client.set_callback(mqtt_callback)

    try:
        client.connect()
    except:  # noqa
        manage_data["mqtt_connect"] = False
        return client
    sub_topics = [config.topics[t] for t in config.topics if "sub" in t]
    for t in sub_topics:
        client.subscribe(t)
    print("connected to {}, subscribed to {}".format(server, sub_topics))
    try:
        cmd_out = '{"timestamp":1}'
        client.publish(config.topics["pub"], cmd_out)
        manage_data["mqtt_connect"] = True
    except:  # noqa
        manage_data["mqtt_connect"] = False
        restart_and_reconnect()
    reset_out()
    return client


def restart_and_reconnect():
    print("Failed to connect to MQTT broker. Reconnecting...")
    if not station.isconnected():
        print("WiFi connection lost!")
        wifi_init()
    for x in range(4):
        config.pins["FAN_POWER"].value(1)
        time.sleep(0.5)
        config.pins["FAN_POWER"].value(0)
        time.sleep(0.5)


def parse_command(cmd):
    datahold = cmd.get("datahold", {})
    data = datahold.get("powerstate")
    if not data:
        return
    if data == "RESET" or datahold.get("RESET"):
        machine.reset()
    else:
        change_state(data)


def send_pong(msg, client):
    client.publish(config.topics["pub_id_pong"], msg)
    return


def send_state(power_state, client):
    ts_tmp = manage_data["ping_timestamp"] + int((utime.ticks_ms() - manage_data["ping_millis"]) / 1000)
    t_com = '{"timestamp":%s, "datahold":{"powerstate":"{%s}"}}' % (
        str(ts_tmp),
        power_state,
    )
    client.publish(config.topics["pub_state"], t_com)


def mqtt_callback(topic, msg):
    if topic == config.topics["sub"]:
        try:
            cmd = ujson.loads(msg)
            parse_command(cmd)
            return
        except:  # noqa
            time.sleep(0.2)
            return
    elif topic == config.topics["sub_ping"]:
        manage_data["ping_msg"] = msg


def main():
    reset_out()
    wifi_init()
    client = mqtt_init()
    while True:
        try:
            client.check_msg()
        except OSError:
            client = mqtt_init()

        if manage_data["send_state"]:
            manage_data["send_state"] = False
            send_state(manage_data["powerstate"], client)

        if manage_data["ping_msg"] != b"":
            send_pong(manage_data["ping_msg"], client)
            manage_data["ping_timestamp"] = (ujson.loads(manage_data["ping_msg"])).get("timestamp", 0)
            manage_data["ping_millis"] = utime.ticks_ms()
            manage_data["ping_msg"] = b""
            continue
        if config.pins["RELAY_IN"].value() == 0 and manage_data["powerstate"] == "OFF":
            change_state("AUX")
            manage_data["send_state"] = True
            continue
        if config.pins["KBD_IN"].value() == 0 and manage_data["powerstate"] == "AUX":
            change_state("PWR")
            manage_data["send_state"] = True


main()
