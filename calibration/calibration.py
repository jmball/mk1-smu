"""Calibrate an ADALM1000 with a Keithley 2400."""

import pathlib
import time
import sys

import numpy as np
import pysmu
import pyvisa
import yaml


test = input(
    "\nDo you want to: [a] perform and write a new calibration to an ADALM1000 or [b] "
    + "test an existing ADALM1000 calibration? "
)

if test == "a":
    cal = input(
        "Is the connected ADALM1000 already running with the default calibration file? [y/n] "
    )
    if cal != "y":
        print(
            "\nCalibration is only possible when starting with the default calibration "
            + "file! Load the default calibration and then try again.\n"
        )
        sys.exit()
elif test == "b":
    cal = "n"
else:
    raise ValueError("Invalid calibration mode selected: {test}. Must be 'a' or 'b'.")

# connect to keithley 2400
print("\nConnecting to Keithley 2400...")
address = "ASRL3::INSTR"
baud = 19200
flow_control = 1
term_char = "\n"

rm = pyvisa.ResourceManager()

keithley2400 = rm.open_resource(
    address,
    baud_rate=baud,
    flow_control=flow_control,
    write_termination=term_char,
    read_termination=term_char,
)
print(f"Keithley ID: {keithley2400.query('*IDN?')}")
print("Connected!")

# connect to M1k
print("\nConnecting to ADALM1000...")
# add all with default settings
session = pysmu.Session()

# make sure only one is connected
if len(session.devices) > 1:
    raise ValueError(
        f"Too many ADALM1000's connected: {len(session.devices)}. Disconnect all "
        + "except the one to be calibrated."
    )
elif len(session.devices) == 0:
    raise ValueError("Device not found. Check connections and try again.")

# setup
mk1 = session.devices[0]
print(f"ADALM1000 ID: {mk1.serial}")
mk1chA = mk1.channels["A"]
mk1chB = mk1.channels["B"]
mk1chA.mode = pysmu.Mode.HI_Z
mk1chB.mode = pysmu.Mode.HI_Z
mk1.set_led(2)
print("Connected!")


# save ADALM1000 formatted calibration file in same folder
cwd = pathlib.Path.cwd()
cal_data_folder = cwd.joinpath("data")
save_file = cal_data_folder.joinpath(f"cal_{int(time.time())}_{mk1.serial}.txt")

# save calibration dictionary in same folder
save_file_dict = cal_data_folder.joinpath(f"cal_{int(time.time())}_{mk1.serial}.yaml")
cal_dict = {
    mk1.serial: {
        "A": {"meas_v": None, "meas_i": None, "source_v": None, "source_i": None},
        "B": {"meas_v": None, "meas_i": None, "source_v": None, "source_i": None},
    }
}

# set global measurement parameters
nplc = 1
settling_delay = 0.005
plf = 50  # power line frequency in Hz

# set mk1 measurement paramters
mk1_nplc = int((nplc / plf) * session.sample_rate)  # number of samples
mk1_settling_delay = int(settling_delay * session.sample_rate)  # number of samples
mk1_samples = mk1_nplc + mk1_settling_delay

# set measurement data using logarithmic spacing
cal_voltages = np.logspace(-3, 0, 25) * 5
cal_voltages = ["0.0000"] + [f"{v:6.4f}" for v in cal_voltages]

cal_currents_ = np.logspace(-4, -1, 25) * 2
cal_currents_0 = [f"{i:6.4f}" for i in cal_currents_]
cal_currents_1 = [f"{i:6.4f}" for i in -cal_currents_]
# for current measurements Keithley and ADALM1000 see opposite polarities.
# cal file has to list +ve current first so for ADALM1000 current measurements
# keithley should start off sourcing -ve current after 0
cal_currents_meas = ["0.0000"] + cal_currents_1 + cal_currents_0
# for ADALM1000 sourcing do the opposite
cal_currents_source = ["0.0000"] + cal_currents_0 + cal_currents_1

# setup keithley
print("\nConfiguring Keithley 2400...")
# reset
keithley2400.write("*RST")
# Disable the output
keithley2400.write(":OUTP OFF")
# Set front terminals
keithley2400.write(":ROUT:TERM FRONT")
# Enable 4-wire sense
keithley2400.write(":SYST:RSEN 1")
# Don't auto-off source after measurement
keithley2400.write(":SOUR:CLE:AUTO OFF")
# Set output-off mode to high impedance
keithley2400.write(":OUTP:SMOD HIMP")
# Make sure output never goes above 20 V
keithley2400.write(":SOUR:VOLT:PROT 20")
# Enable and set concurrent measurements
keithley2400.write(":SENS:FUNC:CONC ON")
keithley2400.write(':SENS:FUNC "CURR", "VOLT"')
# Set read format
keithley2400.write(":FORM:ELEM TIME,VOLT,CURR,STAT")
# Set the integration filter (applies globally for all measurement types)
keithley2400.write(f":SENS:CURR:NPLC {nplc}")
# Set the delay
keithley2400.write(f":SOUR:DEL {settling_delay}")
# Disable autozero
keithley2400.write(":SYST:AZER OFF")
print("Keithley configuration complete!")


def measure_voltage_cal(channel):
    """Perform measurement for voltage measurement calibration of an ADALM100 channel.

    Parameters
    ----------
    channel : {'A', 'B'}
        ADALM1000 channel name: 'A' or 'B'.
    """
    if channel == "A":
        mk1ch = mk1chA
    elif channel == "B":
        mk1ch = mk1chB
    else:
        raise ValueError(f"Invalid channel: {channel}. Must be 'A' or 'B'.")

    print(f"\nPerforming CH{channel} measure voltage calibration measurement...")

    # Autorange keithley source votlage and measure current
    keithley2400.write(f":SOUR:FUNC VOLT")
    keithley2400.write(f":SENS:FUNC 'CURR'")
    keithley2400.write(f":SOUR:VOLT:AUTO ON")
    keithley2400.write(f":SENS:CURR:AUTO ON")

    # set mk1 to source current measure voltage and set current to 0
    mk1ch.mode = pysmu.Mode.HI_Z
    mk1.set_led(6)

    # set keithley to source zero volts and enable output
    keithley2400.write(":SOUR:VOLT 0")
    keithley2400.write(":OUTP ON")
    keithley2400.write(":SYST:AZER ONCE")

    if save_file.exists() is True:
        write_mode = "a"
    else:
        write_mode = "w"

    # measure and save
    with open(save_file, write_mode) as f:
        f.write(f"# Channel {channel}, measure V\n")
        f.write("</>\n")
        # run through the list of voltages
        cal_ch_meas_v = []
        for v in cal_voltages:
            keithley2400.write(f":SOUR:VOLT {v}")
            time.sleep(0.1)
            keithley_data = keithley2400.query_ascii_values(":READ?")
            keithley_v = keithley_data[0]
            mk1_data = mk1ch.get_samples(mk1_samples)
            mk1_vs = [v for v, i in mk1_data[mk1_settling_delay:]]
            mk1_v = sum(mk1_vs) / len(mk1_vs)
            f.write(f"<{keithley_v:6.4f}, {mk1_v:7.5f}>\n")
            cal_ch_meas_v.append([keithley_v, mk1_v])
            print(f"Keithley: {keithley_v:6.4f}, ADALM1000: {mk1_v:7.5f}")

        f.write("<\>\n\n")
        cal_dict[mk1.serial][channel]["meas_v"] = cal_ch_meas_v

    # turn off smu outputs
    mk1ch.mode = pysmu.Mode.HI_Z
    mk1.set_led(2)
    keithley2400.write(":OUTP OFF")

    print("CHA measure voltage calibration measurement complete!")


def measure_current_cal(channel):
    """Perform measurement for current measurement calibration of an ADALM100 channel.

    Parameters
    ----------
    channel : {'A', 'B'}
        ADALM1000 channel name: 'A' or 'B'.
    """
    if channel == "A":
        mk1ch = mk1chA
    elif channel == "B":
        mk1ch = mk1chB
    else:
        raise ValueError(f"Invalid channel: {channel}. Must be 'A' or 'B'.")

    print(f"\nPerforming CH{channel} measure current calibration measurement...")

    # Autorange keithley source votlage and measure current
    keithley2400.write(f":SOUR:FUNC CURR")
    keithley2400.write(f":SENS:FUNC 'VOLT'")
    keithley2400.write(f":SOUR:CURR:AUTO ON")
    keithley2400.write(f":SENS:VOLT:AUTO ON")

    # set mk1 to source current measure voltage and set current to 0
    mk1ch.mode = pysmu.Mode.SVMI
    mk1ch.constant(0)
    mk1.set_led(6)

    # set keithley to source zero volts and enable output
    keithley2400.write(":SOUR:CURR 0")
    keithley2400.write(":OUTP ON")
    keithley2400.write(":SYST:AZER ONCE")

    if save_file.exists() is True:
        write_mode = "a"
    else:
        write_mode = "w"

    # measure and save
    with open(save_file, write_mode) as f:
        f.write(f"# Channel {channel}, measure I\n")
        f.write("</>\n")
        # run through the list of voltages
        cal_ch_meas_i = []
        for i in cal_currents_meas:
            keithley2400.write(f":SOUR:CURR {i}")
            time.sleep(0.1)
            keithley_data = keithley2400.query_ascii_values(":READ?")
            # reverse polarity as SMU's are seeing opposites
            keithley_i = -keithley_data[1]
            mk1_data = mk1ch.get_samples(mk1_samples)
            mk1_is = [i for v, i in mk1_data[mk1_settling_delay:]]
            mk1_i = sum(mk1_is) / len(mk1_is)
            f.write(f"<{keithley_i:6.4f}, {mk1_i:7.5f}>\n")
            cal_ch_meas_i.append([keithley_i, mk1_i])
            print(f"Keithley: {keithley_i:6.4f}, ADALM1000: {mk1_i:7.5f}")

        f.write("<\>\n\n")
        cal_dict[mk1.serial][channel]["meas_i"] = cal_ch_meas_i

    # turn off smu outputs
    mk1ch.mode = pysmu.Mode.HI_Z
    mk1.set_led(2)
    keithley2400.write(":OUTP OFF")

    print(f"CH{channel} measure current calibration measurement complete!")


def source_voltage_cal(channel):
    """Perform measurement for voltage source calibration of an ADALM100 channel.

    Parameters
    ----------
    channel : {'A', 'B'}
        ADALM1000 channel name: 'A' or 'B'.
    """
    if channel == "A":
        mk1ch = mk1chA
    elif channel == "B":
        mk1ch = mk1chB
    else:
        raise ValueError(f"Invalid channel: {channel}. Must be 'A' or 'B'.")

    print(f"\nPerforming CH{channel} source voltage calibration measurement...")

    # Autorange keithley source votlage and measure current
    keithley2400.write(f":SOUR:FUNC CURR")
    keithley2400.write(f":SENS:FUNC 'VOLT'")
    keithley2400.write(f":SOUR:CURR:AUTO ON")
    keithley2400.write(f":SENS:VOLT:AUTO ON")

    # set keithley to source zero volts and enable output
    keithley2400.write(":SOUR:CURR 0")
    keithley2400.write(":OUTP ON")
    keithley2400.write(":SYST:AZER ONCE")

    if save_file.exists() is True:
        write_mode = "a"
    else:
        write_mode = "w"

    # measure and save
    with open(save_file, write_mode) as f:
        f.write(f"# Channel {channel}, source V\n")
        f.write("</>\n")
        # run through the list of voltages
        cal_ch_sour_v = []
        for ix, v in enumerate(cal_voltages):
            mk1ch.mode = pysmu.Mode.SVMI
            mk1ch.constant(float(v))
            mk1ch.get_samples(1)
            mk1ch.mode = pysmu.Mode.SVMI
            if ix == 0:
                mk1.set_led(6)
            time.sleep(0.1)
            keithley_data = keithley2400.query_ascii_values(":READ?")
            keithley_v = keithley_data[0]
            mk1_data = mk1ch.get_samples(mk1_samples)
            mk1_vs = [v for v, i in mk1_data[mk1_settling_delay:]]
            mk1_v = sum(mk1_vs) / len(mk1_vs)
            f.write(f"<{mk1_v:7.5f}, {keithley_v:6.4f}>\n")
            cal_ch_sour_v.append([mk1_v, keithley_v])
            print(f"ADALM1000: {mk1_v:7.5f}, Keithley: {keithley_v:6.4f}")

        f.write("<\>\n\n")
        cal_dict[mk1.serial][channel]["source_v"] = cal_ch_sour_v

    # turn off smu outputs
    mk1.set_led(2)
    keithley2400.write(":OUTP OFF")

    print(f"CH{channel} source voltage calibration measurement complete!")


def source_current_cal(channel):
    """Perform measurement for voltage source calibration of an ADALM100 channel.

    Parameters
    ----------
    channel : {'A', 'B'}
        ADALM1000 channel name: 'A' or 'B'.
    """
    if channel == "A":
        mk1ch = mk1chA
    elif channel == "B":
        mk1ch = mk1chB
    else:
        raise ValueError(f"Invalid channel: {channel}. Must be 'A' or 'B'.")

    print(f"\nPerforming CH{channel} source voltage calibration measurement...")

    # Autorange keithley source votlage and measure current
    keithley2400.write(":SOUR:FUNC VOLT")
    keithley2400.write(":SENS:FUNC 'CURR'")
    keithley2400.write(":SOUR:VOLT:AUTO ON")
    keithley2400.write(":SENS:CURR:AUTO ON")

    # set the current compliance
    keithley2400.write(":SENS:CURR:PROT 0.25")

    # set keithley to source zero volts and enable output
    keithley2400.write(":SOUR:VOLT 0")
    keithley2400.write(":OUTP ON")
    keithley2400.write(":SYST:AZER ONCE")

    if save_file.exists() is True:
        write_mode = "a"
    else:
        write_mode = "w"

    # measure and save
    with open(save_file, write_mode) as f:
        f.write(f"# Channel {channel}, source I\n")
        f.write("</>\n")
        # run through the list of voltages
        cal_ch_sour_i = []
        for ix, i in enumerate(cal_currents_source):
            mk1ch.mode = pysmu.Mode.SIMV
            mk1ch.constant(float(i))
            mk1ch.get_samples(1)
            mk1ch.mode = pysmu.Mode.SIMV
            if ix == 0:
                mk1.set_led(6)
            time.sleep(0.1)
            keithley_data = keithley2400.query_ascii_values(":READ?")
            # reverse polarity as SMU's are seeing opposites
            keithley_i = -keithley_data[1]
            mk1_data = mk1ch.get_samples(mk1_samples)
            mk1_is = [i for v, i in mk1_data[mk1_settling_delay:]]
            mk1_i = sum(mk1_is) / len(mk1_is)
            f.write(f"<{mk1_i:7.5f}, {keithley_i:6.4f}>\n")
            cal_ch_sour_i.append([mk1_i, keithley_i])
            print(f"set: {i}, ADALM1000: {mk1_i:6.4f}, Keithley: {keithley_i:7.5f}")

        f.write("<\>\n\n")
        cal_dict[mk1.serial][channel]["source_i"] = cal_ch_sour_i

    # turn off smu outputs
    mk1.set_led(2)
    keithley2400.write(":OUTP OFF")

    print(f"CH{channel} source voltage calibration measurement complete!")


# perform calibration measurements in exact order required for cal file
input(
    "\nConnect Keithley HI to ADALM1000 CH A and Keithley LO to ADALM1000 GND. Press "
    + "Enter when ready..."
)
measure_voltage_cal("A")
measure_current_cal("A")
source_voltage_cal("A")
input(
    "\nConnect Keithley HI to ADALM1000 CH A and Keithley LO to ADALM1000 2.5 V. Press"
    + " Enter when ready..."
)
source_current_cal("A")

input(
    "\nConnect Keithley HI to ADALM1000 CH B and Keithley LO to ADALM1000 GND. Press "
    + "Enter when ready..."
)
measure_voltage_cal("B")
measure_current_cal("B")
source_voltage_cal("B")
input(
    "\nConnect Keithley HI to ADALM1000 CH B and Keithley LO to ADALM1000 2.5 V. Press"
    + " Enter when ready..."
)
source_current_cal("B")

# # write new calibration to the device
if cal == "y":
    mk1.write_calibraiton(str(save_file))
    print(
        "\nNew calibration was written to the device! Power cycle the device to "
        + "ensure calibration is properly stored before measuring again."
    )
else:
    print("\nNew calibration was not written to the device!")

# export calibration dictionary to a yaml file
with open(save_file_dict, "w") as f:
    data = yaml.dump(cal_dict, f)

print("\nCalibration complete!\n")
