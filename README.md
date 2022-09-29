# inetbox.py

This is a software implementation of a Truma iNet box, a device for controlling mobile heater and AC units by Truma and Alde.

This software is not provided, endorsed, supported, or sponsored by Truma or Alde. It may or may not be working with their products. Please read the [license](./LICENSE) file, in particular:

IN NO EVENT UNLESS REQUIRED BY APPLICABLE LAW OR AGREED TO IN WRITING
WILL ANY COPYRIGHT HOLDER, OR ANY OTHER PARTY WHO MODIFIES AND/OR CONVEYS
THE PROGRAM AS PERMITTED ABOVE, BE LIABLE TO YOU FOR DAMAGES, INCLUDING ANY
GENERAL, SPECIAL, INCIDENTAL OR CONSEQUENTIAL DAMAGES ARISING OUT OF THE
USE OR INABILITY TO USE THE PROGRAM.

That said, it is working for me, and I hope it will work for you, too.

## Hardware Requirements

This has been tested with a Truma Combi 4 and the CP Plus control panel (inet ready). I don't see why this wouldn't be working with the Combi 6 and E models as well.

The software runs on a Raspberry Pi, any newer model should do. This could also be ported to a Pi Pico Microcontroller, but I haven't done that yet.

You need a [LIN to UART Transceiver](https://amzn.to/3E1qITr) (Affiliate Link!) for connecting the Raspberry Pi to the LIN bus. On the transceiver module, the connections are as follows:

 * **LIN** connects to Pin 4 on an RJ12 connector (the one with the 6 pins) going into any port on the Truma Combi heating, or using a [splitter module](https://amzn.to/3dL4bzT) (Affiliate Link!) into the existing connection between Combi and the control panel. Use standard RJ12 cables for the connection. The strand for Pin 4 will be colored green.
 * **GND** (any of the two) connects to a ground connection - e.g. on the power supply.
 * **12V** connects to a 12V power supply that also powers the Combi and CP Plus.
 * **TX** connects to pin 15 on the Raspberry Pi.
 * **RX** connects to pin 14 on the Raspberry Pi (14/15 might be the other way round, not sure).

The other pins (**INH**, **SLP**, second **GND**) are not used.

## Installation

You first need to **enable UART** on the Pi. For this, follow the steps listed under "Configure UART on Raspberry Pi" on [this website](https://www.electronicwings.com/raspberry-pi/raspberry-pi-uart-communication-using-python-and-c#header2) until the step to reboot the Pi.

Then install the software:

 * `pip3 install inetbox_py[truma_service]` is what you normally want to do.
 * `pip3 install inetbox_py` installs just the library in case you want to develop your own code using it. 

**After you have tested that the software works for you**, to install a system service using this software, run **as root**:

```bash
pip3 install inetbox_py[truma_service]
truma_service --install
systemctl enable miqro_truma
systemctl start miqro_truma
```

## Usage

In the following, only the MQTT service will be explained. You need an MQTT broker running (e.g. [Mosquitto](https://mosquitto.org/)) for this to work and you should be familiar with basic MQTT concepts.

Define the MQTT broker by creating the file `/etc/miqro.yml` with the broker settings as follows (adapt as needed):

```yaml
broker:
  host: localhost
  port: 1883
  keepalive: 60
  
log_level: INFO

services: {}

```

To run the service:
```
truma_service
```

If you want to enable debugging, you can set the environment variables `DEBUG_LIN=1`, `DEBUG_PROTOCOL=1`, and `DEBUG_APP=1`, to debug the LIN bus (byte level communication), the protocol layer (handing LIN bus specifics), and the application layer (handling the actual data), respectively.

Example:

`DEBUG_LIN=1 truma_service`

## Initializing

This script plays the role of the inet box. You might need to initialize CP Plus again to make the fake inet box known to the system. This is an easy step that can safely be repeated (no settings are lost): After starting the software, go to the settings menu on the CP Plus and select "PR SET". The display will show "Init..." and after a few seconds, the initialization will be completed.

## MQTT Topics

When started, the service will connect to the LIN bus and publish any status updates acquired from there. When you send a command to modify a setting (e.g., to turn on the heating), the service will send the command to the LIN bus and publish the new status once the setting has been confirmed.

### MQTT topics for receiving status

`service/truma/error` - some error messages are published here

`service/truma/display_status/#` - frequent updates from CP Plus, similar to what is shown on the display. Note that not all values have been decoded yet.

`service/truma/control_status/#` - less frequent updates, but includes values that can be modified. These are the values that would otherwise be available in the Truma inet app.

### Changing settings

In general, publish a message to `service/truma/set/<setting>` (where `<setting>` is one of the settings published in `service/truma/control_status/#`) with the value you want to set. After restarting the service, wait a minute or so until the first set of values has been published before changing settings.

For example:

```bash
mosquitto_pub -t 'service/truma/set/target_temp_water' -m '40'
```
or

```bash
mosquitto_pub -t 'service/truma/set/target_temp_room' -m '10'; mosquitto_pub -t 'service/truma/set/heating_mode' -m 'eco'
```

There are some specifics for certain settings:

 * `target_temp_room` and `heating_mode` must both be enabled for the heating to work. It's best to set both together as in the example above.
 * `target_temp_room` can be set to 0 to turn off the heating, and 5-30 degrees otherwise.
 * `heating_mode` can be set to `off`, `eco` and `high` and defines the fan intensity for room heating.
 * `target_temp_water` must be set to one of `0` (off), `40` (equivalent to selecting 'eco' on the display), `60` ('high'), or `200` (boost)
 * `energy_mix` and `el_power_level` should be set together.
 * `energy_mix` can be one of `none`/`gas`/`electricity`/`mix`
 * `el_power_level` can be set to `0`/`900`/`1800` when electric heating or mix is enabled

## Acknowledgements

This project is based on the work of the [WomoLIN project](https://github.com/muccc/WomoLIN), especially the initial protocol decoding and the inet box log files.
