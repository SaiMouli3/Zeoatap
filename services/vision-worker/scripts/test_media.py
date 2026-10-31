"""Adult-only, openly licensed test photo for the detection smoke test.

NASA/Joel Kowsky, "SpaceX Crew-1 Crew Walkout" (NASA id NHQ202011150013):
four adult astronauts and several adult staff, no children, a still photo
(not footage). NASA imagery is generally not subject to US copyright
(https://www.nasa.gov/nasa-brand-center/images-and-media/); identifiable
people may not be used to imply endorsement, so it is used for tests only.
"""

PHOTO = {
    "file": "test_adults_nasa_crew1.jpg",
    "url": "https://images-assets.nasa.gov/image/NHQ202011150013/NHQ202011150013~medium.jpg",
    "page": "https://images.nasa.gov/details/NHQ202011150013",
    "license": "NASA media (public domain in the US; no endorsement use)",
    "sha256": "0ce236d6e9b007d509daefdcb53eece58fa79bbc37c7b9a3bb73ab6eb0d77ed8",
    "min_adults": 4,  # the four astronauts in the foreground are unoccluded
}
