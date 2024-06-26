from Log import Log

Log(__file__)

from TwitchWebsocket import TwitchWebsocket
from nltk.tokenize import sent_tokenize
import threading, socket, time, logging, re, string

from Settings import Settings
from Database import Database
from Timer import LoopingTimer
import random

logger = logging.getLogger(__name__)


class MarkovChain:
    def __init__(self):
        self.prev_message_t = 0
        self._enabled = True
        # This regex should detect similar phrases as links as Twitch does
        self.link_regex = re.compile("\w+\.[a-z]{2,}")
        # Make a translation table for removing punctuation efficiently
        self.punct_trans_table = str.maketrans("", "", string.punctuation)
        # List of moderators used in blacklist modification, includes broadcaster
        self.set_blacklist()

        # Fill previously initialised variables with data from the settings.txt file
        self.settings = Settings(self)
        self.mod_list = self.settings.mods
        self.db = Database(self.settings.channel)

        # Set up daemon Timer to send help messages
        if self.settings.help_message_timer > 0:
            if self.settings.help_message_timer < 300:
                raise ValueError(
                    "Value for \"HelpMessageTimer\" in must be at least 300 seconds, or a negative number for no help messages.")
            t = LoopingTimer(self.settings.help_message_timer, self.send_help_message)
            t.start()

        # Set up daemon Timer to send automatic generation messages
        if self.settings.automatic_generation_timer > 0:
            if self.settings.automatic_generation_timer < 30:
                raise ValueError(
                    "Value for \"AutomaticGenerationMessage\" in must be at least 30 seconds, or a negative number for no automatic generations.")
            t = LoopingTimer(self.settings.automatic_generation_timer, self.send_automatic_generation_message)
            t.start()

        self.ws = TwitchWebsocket(host=self.settings.host,
                                  port=self.settings.port,
                                  chan=self.settings.channel,
                                  nick=self.settings.nickname,
                                  auth=self.settings.authentication,
                                  callback=self.message_handler,
                                  capability=["commands", "tags"],
                                  live=True)

    def start_bot(self):
        self.ws.start_bot()

    def message_handler(self, m):
        try:
            if m.type == "366":
                logger.info(f"Successfully joined channel: #{m.channel}")
                # Get the list of mods used for modifying the blacklist
                logger.info(f'mods: {self.settings.mods}')
                if (self.settings.startup_messages):
                    self.ws.send_message(random.choice(self.settings.startup_messages))

            elif m.type == "NOTICE":
                logger.info(m.message)

            elif m.type in ("PRIVMSG", "WHISPER"):
                if m.message.startswith("!enable") and (
                        self.check_if_streamer(m) or self.check_if_mod(m) or m.user == "DoctorInsanoPhD"):
                    if self._enabled:
                        self.ws.send_message("The !generate is already enabled.")
                    else:
                        self.ws.send_message("Users can now !generate message again.")
                        self._enabled = True

                elif m.message.startswith("!disable") and (
                        self.check_if_streamer(m) or self.check_if_mod(m) or m.user == "DoctorInsanoPhD"):
                    if self._enabled:
                        self.ws.send_message("Users can now no longer use !generate.")
                        self._enabled = False
                    else:
                        self.ws.send_message("The !generate is already disabled.")

                elif m.message.startswith(("!setcooldown", "!setcd")) and (
                        self.check_if_streamer(m) or self.check_if_mod(m) or m.user == "DoctorInsanoPhD"):
                    split_message = m.message.split(" ")
                    if len(split_message) == 2:
                        try:
                            cooldown = int(split_message[1])
                        except ValueError:
                            self.ws.send_message(f"The parameter must be an integer amount, eg: !setcd 30")
                            return
                        self.settings.cooldown = cooldown
                        Settings.update_cooldown(cooldown)
                        self.ws.send_message(f"The !generate cooldown has been set to {cooldown} seconds.")
                    else:
                        self.ws.send_message(f"Please add exactly 1 integer parameter, eg: !setcd 30.")

            if m.type == "PRIVMSG":

                # Ignore bot messages
                if m.user.lower() in self.settings.denied_users:
                    return

                if self.check_if_generate(m.message):
                    if not self._enabled:
                        if not self.db.check_whisper_ignore(m.user):
                            self.ws.send_whisper(m.user,
                                                 "The !generate has been turned off. !nopm to stop me from whispering you.")
                        return

                    cur_time = time.time()
                    if self.prev_message_t + self.settings.cooldown < cur_time or self.check_if_streamer(
                            m) or self.check_if_mod(m):
                        if self.check_filter(m.message):
                            sentence = "You can't make me say that, you madman!"
                        else:
                            params = m.message.split(" ")[1:]
                            # Generate an actual sentence
                            sentence, success = self.generate(params)
                            if success:
                                # Reset cooldown if a message was actually generated
                                self.prev_message_t = time.time()
                        logger.info(sentence)
                        self.ws.send_message(sentence)
                    else:
                        if not self.db.check_whisper_ignore(m.user):
                            self.ws.send_whisper(m.user,
                                                 f"Cooldown hit: {self.prev_message_t + self.settings.cooldown - cur_time:0.2f} out of {self.settings.cooldown:.0f}s remaining. !nopm to stop these cooldown pm's.")
                        logger.info(
                            f"Cooldown hit with {self.prev_message_t + self.settings.cooldown - cur_time:0.2f}s remaining")
                    return

                # Send help message when requested.
                elif m.message.startswith(("!ghelp", "!genhelp", "!generatehelp")):
                    self.send_help_message()

                # Ignore the message if it is deemed a command
                elif self.check_if_other_command(m.message):
                    return

                # Ignore the message if it contains a link.
                elif self.check_link(m.message):
                    return

                if "emotes" in m.tags:
                    # If the list of emotes contains "emotesv2_", then the message contains a bit emote, 
                    # and we choose not to learn from those messages.
                    if "emotesv2_" in m.tags["emotes"]:
                        return

                    # Replace modified emotes with normal versions, 
                    # as the bot will never have the modified emotes unlocked at the time.
                    for modifier in self.extract_modifiers(m.tags["emotes"]):
                        m.message = m.message.replace(modifier, "")

                # Ignore the message if any word in the sentence is on the ban filter
                if self.check_filter(m.message):
                    logger.warning(f"Sentence contained blacklisted word or phrase:\"{m.message}\"")
                    return

                else:
                    # Try to split up sentences. Requires nltk's 'punkt' resource
                    try:
                        sentences = sent_tokenize(m.message)
                    # If 'punkt' is not downloaded, then download it, and retry
                    except LookupError:
                        logger.debug("Downloading required punkt resource...")
                        import nltk
                        nltk.download('punkt')
                        logger.debug("Downloaded required punkt resource.")
                        sentences = sent_tokenize(m.message)

                    for sentence in sentences:
                        # Get all seperate words
                        words = sentence.split(" ")
                        if "" in words:
                            words = list(filter(lambda x: x != "", words))  # double spaces will lead to invalid rules

                        # If the sentence is too short, ignore it and move on to the next.
                        if len(words) <= self.settings.key_length:
                            continue

                        # Add a new starting point for a sentence to the <START>
                        # self.db.add_rule(["<START>"] + [words[x] for x in range(self.settings.key_length)])
                        self.db.add_start_queue([words[x] for x in range(self.settings.key_length)])

                        # Create Key variable which will be used as a key in the Dictionary for the grammar
                        key = list()
                        for word in words:
                            # Set up key for first use
                            if len(key) < self.settings.key_length:
                                key.append(word)
                                continue
                            # Remove the first word, and add the current word,
                            # so that the key is correct for the next word.
                            key.pop(0)
                            key.append(word)
                        # Add <END> at the end of the sentence
                        self.db.add_rule_queue(key + ["<END>"])

            elif m.type == "WHISPER":
                # Allow people to whisper the bot to disable or enable whispers.
                if m.message == "!nopm":
                    logger.debug(f"Adding {m.user} to Do Not Whisper.")
                    self.db.add_whisper_ignore(m.user)
                    self.ws.send_whisper(m.user, "You will no longer be sent whispers. Type !yespm to reenable. ")

                elif m.message == "!yespm":
                    logger.debug(f"Removing {m.user} from Do Not Whisper.")
                    self.db.remove_whisper_ignore(m.user)
                    self.ws.send_whisper(m.user, "You will again be sent whispers. Type !nopm to disable again. ")

                # Note that I add my own username to this list to allow me to manage the 
                # blacklist in channels of my bot in channels I am not modded in.
                # I may modify this and add a "allowed users" field in the settings file.
                elif m.user.lower() in self.mod_list + ["cubiedev"]:
                    # Adding to the blacklist
                    if self.check_if_our_command(m.message, "!blacklist"):
                        if len(m.message.split()) == 2:
                            # TODO: Remove newly blacklisted word from the Database
                            word = m.message.split()[1].lower()
                            self.blacklist.append(word)
                            logger.info(f"Added `{word}` to Blacklist.")
                            self.write_blacklist(self.blacklist)
                            self.ws.send_whisper(m.user, "Added word to Blacklist.")
                        else:
                            self.ws.send_whisper(m.user,
                                                 "Expected Format: `!blacklist word` to add `word` to the blacklist")

                    # Removing from the blacklist
                    elif self.check_if_our_command(m.message, "!whitelist"):
                        if len(m.message.split()) == 2:
                            word = m.message.split()[1].lower()
                            try:
                                self.blacklist.remove(word)
                                logger.info(f"Removed `{word}` from Blacklist.")
                                self.write_blacklist(self.blacklist)
                                self.ws.send_whisper(m.user, "Removed word from Blacklist.")
                            except ValueError:
                                self.ws.send_whisper(m.user, "Word was already not in the blacklist.")
                        else:
                            self.ws.send_whisper(m.user,
                                                 "Expected Format: `!whitelist word` to remove `word` from the blacklist.")

                    # Checking whether a word is in the blacklist
                    elif self.check_if_our_command(m.message, "!check"):
                        if len(m.message.split()) == 2:
                            word = m.message.split()[1].lower()
                            if word in self.blacklist:
                                self.ws.send_whisper(m.user, "This word is in the Blacklist.")
                            else:
                                self.ws.send_whisper(m.user, "This word is not in the Blacklist.")
                        else:
                            self.ws.send_whisper(m.user,
                                                 "Expected Format: `!check word` to check whether `word` is on the blacklist.")

            elif m.type == "CLEARMSG":
                # If a message is deleted, its contents will be unlearned
                # or rather, the "occurances" attribute of each combinations of words in the sentence
                # is reduced by 5, and deleted if the occurances is now less than 1. 
                self.db.unlearn(m.message)

                # TODO: Think of some efficient way to check whether it was our message that got deleted.
                # If the bot's message was deleted, log this as an error
                # if m.user.lower() == self.nick.lower():
                #    logger.error(f"This bot message was deleted: \"{m.message}\"")

        except Exception as e:
            logger.exception(e)

    def generate(self, params) -> "Tuple[str, bool]":
        if "pineapple" in params:
            return (random.choice([
                "Pineapple belongs on pizza.",
                "Pineapple!? My favorite pizza topping!",
                "Pineapple? On pizza!? Yes, please.",
                "Pizza? Only if it has pineapple on it."
            ]), True)

        # Check for commands or recursion, eg: !generate !generate
        if len(params) > 0:
            if self.check_if_other_command(params[0]):
                return "You can't make me do commands, you madman!", False

        # Get the starting key and starting sentence.
        # If there is more than 1 param, get the last 2 as the key.
        # Note that self.settings.key_length is fixed to 2 in this implementation
        if len(params) > 1:
            key = params[-self.settings.key_length:]
            # Copy the entire params for the sentence
            sentence = params.copy()

        elif len(params) == 1:
            # First we try to find if this word was once used as the first word in a sentence:
            key = self.db.get_next_single_start(params[0])
            if key == None:
                # If this failed, we try to find the next word in the grammar as a whole
                key = self.db.get_next_single_initial(0, params[0])
                if key == None:
                    # Return a message that this word hasn't been learned yet
                    return f"I haven't extracted \"{params[0]}\" from chat yet.", False
            # Copy this for the sentence
            sentence = key.copy()

        else:  # if there are no params
            # Get starting key
            key = self.db.get_start()
            if key:
                # Copy this for the sentence
                sentence = key.copy()
            else:
                # If nothing's ever been said
                return "There is not enough learned information yet.", False

        attempts = 0
        while len(sentence) < self.settings.minimum_sentence_length and attempts < 10:
            generated_sentence = self.generate_sentence(key[:])
            if not generated_sentence:
                key = self.db.get_start()
            else:
                sentence += generated_sentence
            attempts += 1

        # If there were params, but the sentence resulting is identical to the params
        # Then the params did not result in an actual sentence
        # If so, restart without params
        if len(params) > 0 and params == sentence:
            return "I haven't yet learned what to do with \"" + " ".join(
                params[-self.settings.key_length:]) + "\"", False

        return " ".join(sentence), True

    def generate_sentence(self, key):
        sentence = []
        for i in range(self.settings.max_sentence_length - self.settings.key_length):
            # Use key to get next word
            if i == 0:
                # Prevent fetching <END> on the first go
                word = self.db.get_next_initial(i, key)
                print(word)
            else:
                word = self.db.get_next(i, key)

            # Return if next word is the END
            if word in ["<END>", None] and len(sentence) >= self.settings.minimum_sentence_length:
                break

            if word not in ["<END>", None]:
                # Otherwise add the word
                sentence.append(word)
                # Modify the key so on the next iteration it gets the next item
                key.pop(0)
                key.append(word)
        return sentence

    def extract_modifiers(self, emotes: str) -> list:
        output = []
        try:
            while emotes:
                u_index = emotes.index("_")
                c_index = emotes.index(":", u_index)
                output.append(emotes[u_index:c_index])
                emotes = emotes[c_index:]
        except ValueError:
            pass
        return output

    def write_blacklist(self, blacklist) -> None:
        logger.debug("Writing Blacklist...")
        with open("blacklist.txt", "w") as f:
            f.write("\n".join(sorted(blacklist, key=lambda x: len(x), reverse=True)))
        logger.debug("Written Blacklist.")

    def set_blacklist(self) -> None:
        logger.debug("Loading Blacklist...")
        try:
            with open("blacklist.txt", "r") as f:
                self.blacklist = [l.replace("\n", "") for l in f.readlines()]
                logger.debug("Loaded Blacklist.")

        except FileNotFoundError:
            logger.warning("Loading Blacklist Failed!")
            self.blacklist = ["<start>", "<end>"]
            self.write_blacklist(self.blacklist)

    def send_help_message(self) -> None:
        # Send a Help message to the connected chat, as long as the bot wasn't disabled
        if self._enabled:
            logger.info("Help message sent.")
            try:
                self.ws.send_message(
                    "Learn how this bot generates sentences here: https://github.com/CubieDev/TwitchMarkovChain#how-it-works")
            except socket.OSError as error:
                logger.warning(f"[OSError: {error}] upon sending help message. Ignoring.")

    def send_automatic_generation_message(self) -> None:
        # Send an automatic generation message to the connected chat, 
        # as long as the bot wasn't disabled, just like if someone
        # typed "!g" in chat.
        if self._enabled:
            sentence, success = self.generate([])
            if success:
                logger.info(sentence)
                # Try to send a message. Just log a warning on fail
                try:
                    self.ws.send_message(sentence)
                except socket.OSError as error:
                    logger.warning(f"[OSError: {error}] upon sending automatic generation message. Ignoring.")
            else:
                logger.info(
                    "Attempted to output automatic generation message, but there is not enough learned information yet.")

    def check_filter(self, message) -> bool:
        # Returns True if message contains a banned word.
        for word in message.translate(self.punct_trans_table).lower().split():
            if word in self.blacklist:
                return True
        return False

    def check_if_our_command(self, message: str, *commands: "Tuple[str]") -> bool:
        # True if the first "word" of the message is either exactly command, or in the tuple of commands
        return message.split()[0] in commands

    def check_if_generate(self, message) -> bool:
        # True if the first "word" of the message is either !generate or !g.
        return self.check_if_our_command(message, "!generate", "!g")

    def check_if_other_command(self, message) -> bool:
        # Don't store commands, except /me
        return message.startswith(("!", "/", ".")) and not message.startswith("/me")

    def check_if_streamer(self, m) -> bool:
        # True if the user is the streamer
        return m.user == m.channel

    def check_link(self, message) -> bool:
        # True if message contains a link
        return self.link_regex.search(message)

    def check_if_mod(self, m) -> bool:
        return m.user in self.mod_list


if __name__ == "__main__":
    bot = MarkovChain()
    bot.start_bot()
