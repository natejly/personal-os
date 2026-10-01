"""Tests never touch the login Keychain: secrets go to a file in each test's temp data dir."""
import os

os.environ["GRAIN_SECRETS_BACKEND"] = "file"
