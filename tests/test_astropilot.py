from astropilot import AstroPilot

astro_pilot = AstroPilot(project_dir="Project4")

astro_pilot.set_data_description("CAMELS is a very large suite of state-of-the-art cosmological hydrodynamic simulations that evolve mini-universes from z=127 down to z=0. Make a project to use Lya forest from CAMELS")
astro_pilot.get_idea(idea_maker_model="gpt-4o",
                     idea_hater_model="gpt-4o")