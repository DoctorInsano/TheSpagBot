
import json, os, logging
logger = logging.getLogger(__name__)

class Settings:
    """ Loads data from settings.txt into the bot """
    
    PATH = os.path.join(os.getcwd(), "settings.txt")
    
    def __init__(self, bot):
        try:
            data = self.__read_settings()
            self.host = data["Host"]
            self.port = data["Port"]
            self.channel = data["Channel"]
            self.nickname = data["Nickname"]
            self.authentication = data["Authentication"]
            self.denied_users = data.get("DeniedUsers", [])
            self.cooldown = data.get("Cooldown", 20)
            self.key_length = data.get("KeyLength", 2)
            self.max_sentence_length = data.get("MaxSentenceWordAmount", 25)
            self.help_message_timer = data.get("HelpMessageTimer", 7200)
            self.automatic_generation_timer = data.get("AutomaticGenerationTimer", -1)
            self.startup_messages = data.get("StartupMessages", [])
            self.minimum_sentence_length = data.get("MinimumSentenceLength", 2)
            self.mods = data.get("Mods", [])

        except ValueError:
            logger.error("Error in settings file.")
            raise ValueError("Error in settings file.")

        except FileNotFoundError:
            Settings.write_default_settings_file()
            raise ValueError("Please fix your settings.txt file that was just generated.")
    
    def __read_settings(self):
        # Try to load the file using json.
        with open(Settings.PATH, "r") as f:
            settings = f.read()
            data = json.loads(settings)
            # "BannedWords" is only a key in the settings in older versions.
            # We moved to a separate file for blacklisted words.
            if "BannedWords" in data:
                logger.info("Updating Blacklist system to new version...")
                try:
                    with open("blacklist.txt", "r+") as f:
                        logger.info("Moving Banned Words to the blacklist.txt file...")
                        # Read the data, and split by word or phrase, then add BannedWords
                        banned_list = f.read().split("\n") + data["BannedWords"]
                        # Remove duplicates and sort by length, longest to shortest
                        banned_list = sorted(list(set(banned_list)), key=lambda x: len(x), reverse=True)
                        # Clear file, and then write in the new data
                        f.seek(0)
                        f.truncate(0)
                        f.write("\n".join(banned_list))
                        logger.info("Moved Banned Words to the blacklist.txt file.")
                
                except FileNotFoundError:
                    with open("blacklist.txt", "w") as f:
                        logger.info("Moving Banned Words to a new blacklist.txt file...")
                        # Remove duplicates and sort by length, longest to shortest
                        banned_list = sorted(list(set(data["BannedWords"])), key=lambda x: len(x), reverse=True)
                        f.write("\n".join(banned_list))
                        logger.info("Moved Banned Words to a new blacklist.txt file.")
                
                # Remove BannedWords list from data dictionary, and then write it to the settings file
                del data["BannedWords"]

                with open(Settings.PATH, "w") as f:
                    f.write(json.dumps(data, indent=4, separators=(",", ": ")))
                
                logger.info("Updated Blacklist system to new version.")

            # Automatically update the settings.txt to the new version.
            if "HelpMessageTimer" not in data or "AutomaticGenerationTimer" not in data:
                data["HelpMessageTimer"] = data.get("HelpMessageTimer", 7200) # Default is once per 2 hours
                data["AutomaticGenerationTimer"] = data.get("AutomaticGenerationTimer", -1) # Default is never: -1
                
                with open(Settings.PATH, "w") as f:
                    f.write(json.dumps(data, indent=4, separators=(",", ": ")))
        return data

    @staticmethod
    def write_default_settings_file():
        # If the file is missing, create a standardised settings.txt file
        # With all parameters required.
        with open(Settings.PATH, "w") as f:
            standard_dict = {
                                "Host": "irc.chat.twitch.tv",
                                "Port": 6667,
                                "Channel": "#<channel>",
                                "Nickname": "<name>",
                                "Authentication": "oauth:<auth>",
                                "DeniedUsers": ["StreamElements", "Nightbot", "Moobot", "Marbiebot"],
                                "Cooldown": 20,
                                "KeyLength": 2,
                                "MaxSentenceWordAmount": 25,
                                "HelpMessageTimer": 7200,
                                "AutomaticGenerationTimer": -1,
                                "MinimumSentenceLength" : 2,
                                "Mods": "[]"
                            }
            f.write(json.dumps(standard_dict, indent=4, separators=(",", ": ")))

    @staticmethod
    def update_cooldown(cooldown):
        with open(Settings.PATH, "r") as f:
            settings = f.read()
            data = json.loads(settings)
        data["Cooldown"] = cooldown
        with open(Settings.PATH, "w") as f:
            f.write(json.dumps(data, indent=4, separators=(",", ": ")))

