"""Calibrate ADALM1000's with a Keithley 2400 using RS232."""

import argparse
import pathlib
import time
import sys

import numpy as np
import pysmu
import pyvisa
import yaml

sys.path.insert(1, str(pathlib.Path.cwd().parent.parent.joinpath("src")))
import m1k.m1k as m1k


parser = argparse.ArgumentParser()
parser.add_argument(
    "--plf",
    type=float,
    default=50,
    help="Power line frequency, e.g. 50 Hz.",
)
parser.add_argument(
    "--keithley_address",
    type=str,
    default="ASRL3::INSTR",
    help="Keithley 2400 VISA resource address, e.g. ASRL3::INSTR",
)
parser.add_argument(
    "--keithley_baud",
    type=int,
    default=19200,
    help="Keithley 2400 baud rate, e.g. 19200.",
)
parser.add_argument(
    "--keithley_flow",
    type=str,
    default="XON/XOFF",
    choices=["NONE", "XON/XOFF", "RTS/CTS", "DTR/DSR"],
    help="Keithley 2400 flow control setting, e.g. XON/XOFF",
)
parser.add_argument(
    "--keithley_term",
    type=str,
    default="LF",
    choices=["CR", "LF", "CRLF"],
    help="Keithley 2400 termination character, e.g. LF",
)
parser.add_argument(
    "--simv",
    action="store_true",
    default=False,
    help="Include source current, measure voltage calibration. Requires 2.5 V input.",
)
args = parser.parse_args()


cal = input(
    "WARNING: the calibration files generated by this program should only be used if "
    + "the connected device is running the default internal calibration file.\n\nDo "
    + "you wish to continue? [y/n] "
)
if cal != "y":
    print("\nCalibration measurement aborted!.\n")
    sys.exit()

# save ADALM1000 formatted calibration file in data folder
cwd = pathlib.Path.cwd()
cal_data_folder = cwd.joinpath("data")

# connect to keithley 2400
rm = pyvisa.ResourceManager()
flow_controls = {"NONE": 0, "XON/XOFF": 1, "RTS/CTS": 2, "DTR/DSR": 4}
term_chars = {"CR": "\r", "LF": "\n", "CRLF": "\r\n"}

print("\nConnecting to Keithley 2400...")
address = args.keithley_address
baud = args.keithley_baud
flow_control = flow_controls[args.keithley_flow]
term_char = term_chars[args.keithley_term]

keithley2400 = rm.open_resource(
    address,
    baud_rate=baud,
    flow_control=flow_control,
    write_termination=term_char,
    read_termination=term_char,
)
print(f"Keithley ID: {keithley2400.query('*IDN?')}")
print("Connected!")

# connect to m1k's
print("\nConnecting to SMU...")
smu = m1k.smu(plf=args.plf, ch_per_board=2)

# get board serial mapping
board_mapping_file = cwd.parent.joinpath("board_mapping.yaml")
with open(board_mapping_file, "r") as f:
    board_mapping = yaml.load(f, Loader=yaml.FullLoader)

# get list of serials in channel order
serials = []
for i in range(len(board_mapping)):
    # channel mapping file is 1-indexed
    serials.append(board_mapping[i])

# connect boards
smu.connect(serials=serials)

# set global measurement parameters
nplc = 1
settling_delay = 0.005

# set m1k measurement paramters
smu.nplc = nplc
smu.settling_delay = settling_delay

for board in range(smu.num_boards):
    print(f"SMU board {board} ID: {smu.get_channel_id(2 * board)}")
print("Connected!")

# set measurement data using logarithmic spacing
cal_voltages = np.logspace(-3, 0, 25) * 5
cal_voltages = [f"{v:6.4f}" for v in cal_voltages]

cal_currents_ = np.logspace(-4, -1, 25) * 2
cal_currents_0 = [f"{i:6.4f}" for i in cal_currents_]
cal_currents_1 = [f"{i:6.4f}" for i in -cal_currents_]
# for current measurements Keithley and ADALM1000 see opposite polarities.
# cal file has to list +ve current first so for ADALM1000 current measurements
# keithley should start off sourcing -ve current after 0
cal_currents_meas = cal_currents_1 + cal_currents_0
# for ADALM1000 sourcing do the opposite
cal_currents_source = cal_currents_0 + cal_currents_1

# setup keithley
print("\nConfiguring Keithley 2400...")
# reset
keithley2400.write("*RST")
# Disable the output
keithley2400.write(":OUTP OFF")
# Disable beeper
keithley2400.write(":SYST:BEEP:STAT OFF")
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


def measure_voltage_cal(smu, channel, save_file):
    """Perform measurement for voltage measurement calibration of an ADALM100 channel.

    Parameters
    ----------
    smu : m1k.smu
        SMU object.
    channel : int
        SMU channel number.
    save_file : str or pathlib.Path
        Path to save file formatted for internal calibration.
    """
    global cal_dict

    print(f"\nPerforming CH{channel + 1} measure voltage calibration measurement...")

    # get smu sub-channel letter
    dev_channel = smu.channel_settings[channel]["dev_channel"]

    # Autorange keithley source votlage and measure current
    keithley2400.write(":SOUR:FUNC VOLT")
    keithley2400.write(':SENS:FUNC "CURR"')
    keithley2400.write(":SOUR:VOLT:RANG:AUTO ON")
    keithley2400.write(":SENS:CURR:RANG:AUTO ON")

    # set smu to measure voltage in high impedance mode
    smu.enable_output(False, channel)

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

            smu_v = smu.measure(channel, measurement="dc")[channel][0][0]

            f.write(f"<{keithley_v:6.4f}, {smu_v:7.5f}>\n")
            cal_ch_meas_v.append([keithley_v, smu_v])
            print(f"Keithley: {keithley_v:6.4f}, SMU: {smu_v:7.5f}")

        f.write("<\>\n\n")
        cal_dict[dev_channel]["meas_v"] = cal_ch_meas_v

    # turn off smu outputs
    keithley2400.write(":SOUR:VOLT 0")
    keithley2400.write(":OUTP OFF")

    print(f"CH{channel + 1} measure voltage calibration measurement complete!")


def measure_current_cal(smu, channel, save_file):
    """Perform measurement for current measurement calibration of an ADALM100 channel.

    Parameters
    ----------
    smu : m1k.smu
        SMU object.
    channel : int
        SMU channel number.
    save_file : str or pathlib.Path
        Path to save file formatted for internal calibration.
    """
    global cal_dict

    print(f"\nPerforming CH{channel + 1} measure current calibration measurement...")

    # get smu sub-channel letter
    dev_channel = smu.channel_settings[channel]["dev_channel"]

    # Autorange keithley source votlage and measure current
    keithley2400.write(":SOUR:FUNC CURR")
    keithley2400.write(":SENS:FUNC 'VOLT'")
    keithley2400.write(":SOUR:CURR:RANG:AUTO ON")
    keithley2400.write(":SENS:VOLT:RANG:AUTO ON")

    # set m1k to source voltage, measure current and set voltage to 0
    smu.configure_dc({channel: 0}, source_mode="v")
    smu.enable_output(True, channel)

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

            smu_i = smu.measure(channel, measurement="dc")[channel][0][1]

            f.write(f"<{keithley_i:6.4f}, {smu_i:7.5f}>\n")
            cal_ch_meas_i.append([keithley_i, smu_i])
            print(f"Keithley: {keithley_i:6.4f}, SMU: {smu_i:7.5f}")

        f.write("<\>\n\n")
        cal_dict[dev_channel]["meas_i"] = cal_ch_meas_i

    # turn off smu outputs
    smu.enable_output(False, channel)
    keithley2400.write(":SOUR:CURR 0")
    keithley2400.write(":OUTP OFF")

    print(f"CH{channel + 1} measure current calibration measurement complete!")


def source_voltage_cal(smu, channel, save_file):
    """Perform measurement for voltage source calibration of an ADALM100 channel.

    Parameters
    ----------
    smu : m1k.smu
        SMU object.
    channel : int
        SMU channel number.
    save_file : str or pathlib.Path
        Path to save file formatted for internal calibration.
    """
    global cal_dict

    print(f"\nPerforming CH{channel + 1} source voltage calibration measurement...")

    # get smu sub-channel letter
    dev_channel = smu.channel_settings[channel]["dev_channel"]

    # Autorange keithley source votlage and measure current
    keithley2400.write(":SOUR:FUNC CURR")
    keithley2400.write(":SENS:FUNC 'VOLT'")
    keithley2400.write(":SOUR:CURR:RANG:AUTO ON")
    keithley2400.write(":SENS:VOLT:RANG:AUTO ON")

    # set smu to source voltage, measure current and set voltage to 0
    smu.configure_dc({channel: 0}, source_mode="v")
    smu.enable_output(True, channel)

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
        for v in cal_voltages:
            smu.configure_dc({channel: float(v)}, source_mode="v")
            time.sleep(0.1)

            keithley_data = keithley2400.query_ascii_values(":READ?")
            keithley_v = keithley_data[0]

            smu_v = smu.measure(channel, measurement="dc")[channel][0][0]

            f.write(f"<{smu_v:7.5f}, {keithley_v:6.4f}>\n")
            cal_ch_sour_v.append([v, smu_v, keithley_v])
            print(f"SMU: {smu_v:7.5f}, Keithley: {keithley_v:6.4f}")

        f.write("<\>\n\n")
        cal_dict[dev_channel]["source_v"] = cal_ch_sour_v

    # turn off smu outputs
    smu.enable_output(False, channel)
    keithley2400.write(":OUTP OFF")

    print(f"CH{channel + 1} source voltage calibration measurement complete!")


def source_current_cal(smu, channel, save_file):
    """Perform measurement for current source calibration of an ADALM100 channel.

    Parameters
    ----------
    smu : m1k.smu
        SMU object.
    channel : int
        SMU channel number.
    save_file : str or pathlib.Path
        Path to save file formatted for internal calibration.
    """
    global cal_dict

    print(f"\nPerforming CH{channel + 1} source current calibration measurement...")

    # get smu sub-channel letter
    dev_channel = smu.channel_settings[channel]["dev_channel"]

    # Autorange keithley source votlage and measure current
    keithley2400.write(":SOUR:FUNC VOLT")
    keithley2400.write(":SENS:FUNC 'CURR'")
    keithley2400.write(":SOUR:VOLT:RANG:AUTO ON")
    keithley2400.write(":SENS:CURR:RANG:AUTO ON")

    # set smu to source current, measure voltage and set current to 0
    smu.configure_dc({channel: 0}, source_mode="i")
    smu.enable_output(True, channel)

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
        for i in cal_currents_source:
            smu.configure_dc({channel: float(i)}, source_mode="i")
            time.sleep(0.1)

            keithley_data = keithley2400.query_ascii_values(":READ?")
            # reverse polarity as SMU's are seeing opposites
            keithley_i = -keithley_data[1]

            smu_i = smu.measure(channel, measurement="dc")[channel][0][1]

            f.write(f"<{smu_i:7.5f}, {keithley_i:6.4f}>\n")
            cal_ch_sour_i.append([i, smu_i, keithley_i])
            print(f"set: {i}, SMU: {smu_i:6.4f}, Keithley: {keithley_i:7.5f}")

        f.write("<\>\n\n")
        cal_dict[dev_channel]["source_i"] = cal_ch_sour_i

    # turn off smu outputs
    smu.enable_output(False, channel)
    keithley2400.write(":OUTP OFF")

    print(f"CH{channel + 1} source voltage calibration measurement complete!")


def channel_cal(smu, channel, save_file):
    """Run all calibration measurements for a channel.

    Parameters
    ----------
    smu : m1k.smu
        SMU object.
    channel : int
        SMU channel number.
    save_file : str or pathlib.Path
        Path to save file formatted for internal calibration.
    """
    input(
        f"\nConnect Keithley HI to SMU CH {channel + 1} HI and Keithley LO to SMU CH "
        + f"{channel + 1} GND. Press Enter when ready..."
    )
    measure_voltage_cal(smu, channel, save_file)
    source_voltage_cal(smu, channel, save_file)
    measure_current_cal(smu, channel, save_file)

    if args.simv is True:
        input(
            f"\nConnect Keithley HI to SMU CH {channel + 1} HI and Keithley LO to SMU "
            + f"CH {channel + 1} 2.5 V. Press Enter when ready..."
        )
        source_current_cal(smu, channel, save_file)


# perform calibration measurements in exact order required for cal file
t = time.time()
for board in range(smu.num_boards):
    board_serial = smu.get_channel_id(2 * board)

    cal = input(f"\nDo you wish to calibrate {board_serial}? [y/n] ")

    if cal == "y":
        # m1k internal calibration file
        save_file = cal_data_folder.joinpath(f"cal_{int(t)}_{board_serial}.txt")

        # save calibration dictionary in same folder
        save_file_dict = cal_data_folder.joinpath(f"cal_{int(t)}_{board_serial}.yaml")
        cal_dict = {
            "A": {"meas_v": None, "meas_i": None, "source_v": None, "source_i": None},
            "B": {"meas_v": None, "meas_i": None, "source_v": None, "source_i": None},
        }

        # get the channel numbers as seen from hardware, i.e. 1-indexed
        channel_A_num = 2 * board
        channel_B_num = 2 * board + 1

        # run calibrations
        channel_cal(smu, channel_A_num, save_file)
        channel_cal(smu, channel_B_num, save_file)

        # export calibration dictionary to a yaml file
        with open(save_file_dict, "w") as f:
            yaml.dump(cal_dict, f)

smu.set_leds(R=True)

print("\nCalibration measurement complete!\n")
