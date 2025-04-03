# SPDX-FileCopyrightText: 2023 Adafruit Industries
# SPDX-FileCopyrightText: 2023 Brent Rubell for Adafruit Industries
# SPDX-FileCopyrightText: 2023 Jeff Epler for Adafruit Industries
# SPDX-FileCopyrightText: 2023 Limor Fried for Adafruit Industries
# SPDX-FileCopyrightText: 2025 Sophia Anderson for USDA Agricultural Research Service - Biosciences Research Lab
#
# Based on the Adafruit Memento Doorbell Camera and PyCamera Timelapse examples
#
# SPDX-License-Identifier: MIT
""" Camera with Adafruit IO integration and button navigation. """

import time
import os
import ssl
import binascii
import adafruit_pycamera
import wifi
import socketpool
import adafruit_requests
import displayio
from adafruit_io.adafruit_io import IO_HTTP, AdafruitIO_RequestError
import rtc
import digitalio
import adafruit_ntp
import bitmaptools
import gifio
import ulab.numpy as np
import board
import adafruit_logging as logging
import gc

# Wifi details are in settings.toml file, also,
# timezone info should be included to allow local time and DST adjustments
# UTC_OFFSET, if present, will override TZ and DST and no API query will be done
# UTC_OFFSET=-25200
# # TZ="America/Phoenix"

# Use YOUR Wi-Fi environment variables
WIFI_SSID = os.getenv("CIRCUITPY_WIFI_SSID")
WIFI_PASSWORD = os.getenv("CIRCUITPY_WIFI_PASSWORD")

# Create a socket pool (used for networking)
pool = socketpool.SocketPool(wifi.radio)

# Track last Wi-Fi check
last_wifi_check = 0
WIFI_CHECK_INTERVAL = 60  # Only check Wi-Fi every 60 seconds (adjustable)

def check_internet(host="8.8.8.8", port=53, timeout=3):
    """Checks if the device has an active internet connection."""
    try:
        conn = pool.socket(pool.AF_INET, pool.SOCK_STREAM)
        conn.settimeout(timeout)
        conn.connect((host, port))
        conn.close()
        return True
    except Exception:
        return False
    
def check_wifi():
    """Checks Wi-Fi connection periodically (does NOT slow down camera)."""
    global last_wifi_check

    # Only check Wi-Fi every X seconds (prevents lag)
    if time.monotonic() - last_wifi_check > WIFI_CHECK_INTERVAL:
        last_wifi_check = time.monotonic()  # Update last check time
        if not wifi.radio.connected:
            print("Wi-Fi lost! Attempting to reconnect...")
            connect_wifi()  # Only reconnect if Wi-Fi is lost

def reset_wifi():
    """Resets Wi-Fi if connection slows down over time."""
    print("[WIFI] Resetting Wi-Fi radio due to slow performance...")
    try:
        wifi.radio.enabled = False
        time.sleep(2)
        wifi.radio.enabled = True
    except Exception as e:
        print(f"[WIFI] Error during reset: {e}")
    connect_wifi()
is_reconnecting = False

def connect_wifi():
    """Attempts to connect to Wi-Fi and retries in a background loop."""
    global is_reconnecting
    if is_reconnecting:
        return  # Skip if a connection attempt is already in progress

    is_reconnecting = True  # Set flag
    retry_delay = 2  # Start with 2 seconds

    while True:  # Keep retrying indefinitely
        try:
            print(f" Connecting to Wi-Fi: {WIFI_SSID} ...")
            wifi.radio.connect(WIFI_SSID, WIFI_PASSWORD)
            print(f" Connected to Wi-Fi: {WIFI_SSID} ({wifi.radio.ipv4_address})")
            is_reconnecting = False  # Reset flag
            return True  # Connection successful
        except Exception as e:
            print(f" Wi-Fi connection failed: {e}")
            print(f" Retrying in {retry_delay} seconds...")
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 60)  # Exponential backoff (max 60 sec)

def check_memory():
    """Monitor memory usage and force garbage collection if needed."""
    free_mem = gc.mem_free()
    print(f"[MEMORY] Free: {free_mem} bytes")
    
    # If free memory is critically low, force garbage collection
    if free_mem < 10_000:  # Adjust threshold based on your board
        print("[MEMORY] Running garbage collection...")
        gc.collect()
        print(f"[MEMORY] After GC: {gc.mem_free()} bytes")

# Ensure Wi-Fi is connected before starting
connect_wifi()

# === Network Diagnostics (Safe) ===
print("Wi-Fi connected!")
print("IP Address:", wifi.radio.ipv4_address)

# Check DNS & internet connectivity before continuing
print("Checking internet connectivity...")
if not check_internet():
    print("Internet not reachable — DNS may be blocked or unavailable.")
    print("Try switching to a mobile hotspot or home Wi-Fi.")
    while True:
        time.sleep(1)  # Halt here so user sees the error
else:
    print("Internet reachable!")

# === Wi-Fi and Adafruit IO Setup ===
aio_username = os.getenv("ADAFRUIT_AIO_USERNAME")
aio_key = os.getenv("ADAFRUIT_AIO_KEY")

# Create Network Connection
requests = adafruit_requests.Session(pool, ssl.create_default_context())

# Initialize Adafruit IO HTTP API
io = IO_HTTP(aio_username, aio_key, requests)

# Adafruit IO feed configuration
try:
    feed_camera = io.get_feed("camera")
    feed_trigger = io.get_feed("camera-trigger")
    print("Camera connected to Adafruit IO successfully")
except AdafruitIO_RequestError as exception:
    feed_camera = io.create_new_feed("camera")
    feed_trigger = io.create_new_feed("camera-trigger")
    print(f"RuntimeError during Adafruit IO connection: {exception}")
    
### Initialize the PyCamera ###
pycam = adafruit_pycamera.PyCamera()

print("Camera initialized and ready!")

# Provide startup feedback
pycam.tone(800, 0.1)
pycam.tone(1200, 0.05)

settings = (
    None,
    "resolution",
    "effect",
    "mode",
    "led_level",
    "led_color",
    "timelapse_rate",
)
curr_setting = 0

# Override the timelapse_rates
pycam.timelapse_rates = (
    5,         # 5 seconds
    10,        # 10 seconds
    20,        # 20 seconds
    30,        # 30 seconds
    60,        # 1 minute
    90,        # 1.5 minutes
    60 * 2,    # 2 minutes
    60 * 3,    # 3 minutes
    60 * 4,    # 4 minutes
    60 * 5,    # 5 minutes
    60 * 10,   # 10 minutes
    60 * 15,   # 15 minutes
    60 * 30,   # 30 minutes
    60 * 60,   # 1 hour
    60 * 120,  # 2 hours
    60 * 240,  # 4 hours
    60 * 480,  # 8 hours
    60 * 960,  # 16 hours
    60 * 1440, # 24 hours
)

print("Starting!")
# pycam.tone(200, 0.1)
last_frame = displayio.Bitmap(pycam.camera.width, pycam.camera.height, 65535)
onionskin = displayio.Bitmap(pycam.camera.width, pycam.camera.height, 65535)
timelapse_remaining = None
timelapse_timestamp = None

capture_count = 0
def capture_send_image():
    global capture_count
    """Captures an image and sends it to Adafruit IO with backoff retries."""

    if not wifi.radio.connected:
        print("[UPLOAD] No Wi-Fi! Reconnecting...")
        connect_wifi()

    try:
        jpeg = pycam.capture_into_jpeg()
        if jpeg:
            encoded_data = binascii.b2a_base64(jpeg).strip().decode("utf-8")
            
            max_retries = 5
            delay = 2  # Start with 2 seconds
            for attempt in range(max_retries):
                try:
                    if attempt > 0:
                        print(f"[UPLOAD] Retrying... Attempt {attempt+1}")
                    io.send_data(feed_camera["key"], encoded_data)
                    print("[UPLOAD] Success!")

                    # **THIS IS THE CRUCIAL PART FOR EMAIL NOTIFICATIONS**
                    print("Sending trigger to camera-trigger feed...")
                    io.send_data(feed_trigger["key"], 1)  # Notify Adafruit IO
                    print("Trigger sent successfully!")

                    break
                except Exception as e:
                    print(f"[UPLOAD] Failed: {e}")
                    time.sleep(delay)
                    delay = min(delay * 2, 60)  # Exponential backoff (max 60 sec)

    except Exception as e:
        print(f"[UPLOAD] Capture failed: {e}")

    finally:
        capture_count += 1
        if capture_count % 5 == 0:  # Only collect every 5 images
            gc.collect()

# Main loop
while True:
    if time.monotonic() % 300 < 1:  # Every 5 minutes
        check_memory()
    
    check_wifi()  # Check Wi-Fi periodically in the background
    
    pycam.blit(pycam.continuous_capture())  # Keep live preview active
    pycam.keys_debounce()
    
    if pycam.mode_text == "STOP" and pycam.stop_motion_frame != 0:
        # alpha blend
        new_frame = pycam.continuous_capture()
        bitmaptools.alphablend(
            onionskin, last_frame, new_frame, displayio.Colorspace.RGB565_SWAPPED
        )
        pycam.blit(onionskin)
    elif pycam.mode_text == "GBOY":
        bitmaptools.dither(
            last_frame, pycam.continuous_capture(), displayio.Colorspace.RGB565_SWAPPED
        )
        pycam.blit(last_frame)
    elif pycam.mode_text == "LAPS":
        if timelapse_remaining is None:
            pycam.timelapsestatus_label.text = "STOP"
        else:
            timelapse_remaining = timelapse_timestamp - time.monotonic()
            pycam.timelapsestatus_label.text = f"{timelapse_remaining}s /    "
        # Manually updating the label text a second time ensures that the label
        # is re-painted over the blitted preview.
        pycam.timelapse_rate_label.text = pycam.timelapse_rate_label.text
        pycam.timelapse_submode_label.text = pycam.timelapse_submode_label.text

        # only in high power mode do we continuously preview
        if (timelapse_remaining is None) or (
            pycam.timelapse_submode_label.text == "HiPwr"
        ):
            pycam.blit(pycam.continuous_capture())
        if pycam.timelapse_submode_label.text == "LowPwr" and (
            timelapse_remaining is not None
        ):
            pycam.display.brightness = 0.05
        else:
            pycam.display.brightness = 1
        pycam.display.refresh()

        if timelapse_remaining is not None and timelapse_remaining <= 0:
            # no matter what, show what was just on the camera
            pycam.blit(pycam.continuous_capture())
            # pycam.tone(200, 0.1) # uncomment to add a beep when a photo is taken
            try:
                pycam.display_message("Snap!", color=0x0000FF)
               
                # Capture and send the image to Adafruit IO
                capture_send_image()
                
            except TypeError as e:
                pycam.display_message("Failed", color=0xFF0000)
                time.sleep(0.5)
            except RuntimeError as e:
                pycam.display_message("Error\nNo SD Card", color=0xFF0000)
                time.sleep(0.5)
            pycam.live_preview_mode()
            pycam.display.refresh()
            pycam.blit(pycam.continuous_capture())
            timelapse_timestamp = (
                time.monotonic() + pycam.timelapse_rates[pycam.timelapse_rate] + 1
            )
    else:
        pycam.blit(pycam.continuous_capture())
    # print("\t\t", capture_time, blit_time)
    
    pycam.keys_debounce()
    
     # Handle shutter button actions
    if pycam.shutter.long_press:
        print("FOCUS")
        pycam.autofocus()
        print(pycam.autofocus_status)
     
    if pycam.shutter.short_count:
        print("Shutter released")
        pycam.led_level = 4  # Activate the LED
        pycam.led_color = 0  # Set the LED color, if applicable
        pycam.display_message("Snap!", color=0x00DD00)  # Display a snap message on screen
        pycam.tone(1200, 0.05)  # Play a higher-pitched tone to indicate capture
        pycam.tone(1600, 0.05)  # Play another tone immediately after
        time.sleep(0.5)  # Delay to ensure the 'flash' is on long enough to illuminate the scene
        try:
            capture_send_image()  # Attempt to capture and send the image to Adafruit IO
        
        except TypeError as exception:
            pycam.display_message("Failed", color=0xFF0000)  # Display failure message for TypeError
            print(f"TypeError during image capture: {exception}")
        except RuntimeError as exception:
            pycam.display_message("Error\nNo SD Card", color=0xFF0000)  # Display SD card error message
            print(f"RuntimeError during image capture: {exception}")
        finally:
            pycam.led_level = 0  # Ensure the LED is turned off regardless of capture outcome
            print("LED turned off.")  # Confirm LED status in console
            pycam.live_preview_mode()  # Return the camera to live preview mode
            print("Camera returned to live preview mode.")

        if pycam.mode_text == "GBOY":
            try:
                f = pycam.open_next_image("gif")
            except RuntimeError as e:
                pycam.display_message("Error\nNo SD Card", color=0xFF0000)
                time.sleep(0.5)
                continue

            with gifio.GifWriter(
                f,
                pycam.camera.width,
                pycam.camera.height,
                displayio.Colorspace.RGB565_SWAPPED,
                dither=True,
            ) as g:
                g.add_frame(last_frame, 1)

        if pycam.mode_text == "GIF":
            try:
                f = pycam.open_next_image("gif")
            except RuntimeError as e:
                pycam.display_message("Error\nNo SD Card", color=0xFF0000)
                time.sleep(0.5)
                continue
            i = 0
            ft = []
            pycam._mode_label.text = "RECORDING"  # pylint: disable=protected-access

            pycam.display.refresh()
            with gifio.GifWriter(
                f,
                pycam.camera.width,
                pycam.camera.height,
                displayio.Colorspace.RGB565_SWAPPED,
                dither=True,
            ) as g:
                t00 = t0 = time.monotonic()
                while (i < 15) or not pycam.shutter_button.value:
                    i += 1
                    _gifframe = pycam.continuous_capture()
                    g.add_frame(_gifframe, 0.12)
                    pycam.blit(_gifframe)
                    t1 = time.monotonic()
                    ft.append(1 / (t1 - t0))
                    print(end=".")
                    t0 = t1
            pycam._mode_label.text = "GIF"  # pylint: disable=protected-access
            print(f"\nfinal size {f.tell()} for {i} frames")
            print(f"average framerate {i / (t1 - t00)}fps")
            print(f"best {max(ft)} worst {min(ft)} std. deviation {np.std(ft)}")
            f.close()
            pycam.display.refresh()
            
    if pycam.card_detect.fell:
        print("SD card removed")
        pycam.unmount_sd_card()
        pycam.display.refresh()

    if pycam.card_detect.rose:
        print("SD card inserted")
        pycam.display_message("Mounting\nSD Card", color=0xFFFFFF)
        for _ in range(3):
            try:
                print("Mounting card")
                pycam.mount_sd_card()
                print("Success!")
                break
            except OSError as exception:
                print("Retrying!", exception)
                time.sleep(0.5)
        else:
            pycam.display_message("SD Card\nFailed!", color=0xFF0000)
            time.sleep(0.5)
        pycam.display.refresh()
    
    if pycam.up.fell:
        print("UP")
        key = settings[curr_setting]
        if key:
            print("getting", key, getattr(pycam, key))
            setattr(pycam, key, getattr(pycam, key) + 1)
    if pycam.down.fell:
        print("DN")
        key = settings[curr_setting]
        if key:
            setattr(pycam, key, getattr(pycam, key) - 1)
    if pycam.right.fell:
        print("RT")
        curr_setting = (curr_setting + 1) % len(settings)
        if pycam.mode_text != "LAPS" and settings[curr_setting] == "timelapse_rate":
            curr_setting = (curr_setting + 1) % len(settings)
        print(settings[curr_setting])
        # new_res = min(len(pycam.resolutions)-1, pycam.get_resolution()+1)
        # pycam.set_resolution(pycam.resolutions[new_res])
        pycam.select_setting(settings[curr_setting])
    if pycam.left.fell:
        print("LF")
        curr_setting = (curr_setting - 1 + len(settings)) % len(settings)
        if pycam.mode_text != "LAPS" and settings[curr_setting] == "timelaps_rate":
            curr_setting = (curr_setting + 1) % len(settings)
        print(settings[curr_setting])
        pycam.select_setting(settings[curr_setting])
        # new_res = max(1, pycam.get_resolution()-1)
        # pycam.set_resolution(pycam.resolutions[new_res])
    if pycam.select.fell:
        print("SEL")
        if pycam.mode_text == "LAPS":
            pycam.timelapse_submode += 1
            pycam.display.refresh()
    if pycam.ok.fell:
        print("OK")
        if pycam.mode_text == "LAPS":
            if timelapse_remaining is None:  # stopped
                print("Starting timelapse")
                timelapse_remaining = pycam.timelapse_rates[pycam.timelapse_rate]
                timelapse_timestamp = time.monotonic() + timelapse_remaining + 1
                # dont let the camera take over auto-settings
                saved_settings = pycam.get_camera_autosettings()
                # print(f"Current exposure {saved_settings=}")
                pycam.set_camera_exposure(saved_settings["exposure"])
                pycam.set_camera_gain(saved_settings["gain"])
                pycam.set_camera_wb(saved_settings["wb"])
            else:  # is running, turn off
                print("Stopping timelapse")

                timelapse_remaining = None
                pycam.camera.exposure_ctrl = True
                pycam.set_camera_gain(None)  # go back to autogain
                pycam.set_camera_wb(None)  # go back to autobalance
                pycam.set_camera_exposure(None)  # go back to auto shutter
   
    time.sleep(0.1)  # Small delay for responsiveness
