"""Pinned task pose and its matching dataset images. No device access."""
from deployment_release import reference_images, selected_release, VIEWS

RELEASE = selected_release()
DATA = RELEASE.data
DEMO_ID, DEMO_FRAME = RELEASE.episode, RELEASE.frame
DEMO = RELEASE.demo

def reference(episode, frame):
    return reference_images(RELEASE, episode, frame)
