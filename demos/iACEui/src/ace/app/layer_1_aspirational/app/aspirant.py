from base.base_layer import BaseLayer
import aio_pika
import logging
from settings import settings
from base.amqp.exchange import create_exchange
from base.prompts import Prompt, MissionCompletionPrompt
import prompts as p
from identity import primary_directive
import re
import uvicorn
from fastapi import FastAPI


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class Layer1Aspirant(BaseLayer):
    # This is the mission proposed by the user.
    mission: str = "Deny all requests"
    process_messages: bool = False

    def get_primary_directive(self):
        return primary_directive

    # These messages come from the user.
    async def control_bus_message_handler(self, message: aio_pika.IncomingMessage):
        self.mission = message.body.decode()
        logger.info(f"Mission from user: {self.mission}")
        judgement = await self._render_judgement(self.mission)
        
        logger.info(f"{judgement=}")

        if self._extract_judgement(judgement) == "allow":
            logger.info("this mission is allowed...")
            
            await self._publish(
                queue_name=self.settings.control_bus_pub_queue,
                message=f"[User Mission] {self.mission}",
                destination_bus="Control Bus",
                source_bus="Control Bus",            
            )

        else:
            logger.info("this mission is not allowed")

            await self._publish(
                queue_name=self.settings.data_bus_pub_queue,
                message=judgement,
                destination_bus="Data Bus",
                source_bus="Control Bus",     
            )
            self.mission = None

        await message.ack()


    # These are northbound so they come from the layer below "Global Strategy"
    async def data_bus_message_handler(self, message: aio_pika.IncomingMessage):
        msg = message.body.decode()
        logger.info(f"Message from Global Strategy (Layer 2): {msg}")

        judgement = await self._render_judgement(msg)
        
        logger.info(f"{judgement=}")

        if self._extract_judgement(judgement) == "allow":
            status = self._is_mission_complete(msg)

            if self._extract_status(status) == "complete":
                logger.info("mission completed")
                await self._publish(
                    queue_name=self.settings.data_bus_pub_queue,
                    message=msg,
                    destination_bus="Data Bus",
                    source_bus="Data Bus",  
                )
            else: 
                logger.info("mission not yet completed")
                await self._publish(
                    queue_name=self.settings.control_bus_pub_queue,
                    message=status,
                    destination_bus="Control Bus",
                    source_bus="Data Bus",  
                )
        else:
            await self._publish(
                queue_name=self.settings.control_bus_pub_queue,
                  message=judgement,
            )
        await message.ack()


    def _is_mission_complete(self, message):

        prompt = MissionCompletionPrompt(
            source="Data Bus Message",
            message=message,
            mission=self.mission,
            response_format=p.MISSION_COMPLETE_RESPONSE_FORMAT,
        ).generate_prompt()

        mission_status = self._generate_completion(new_message=prompt)
        return mission_status


    async def _render_judgement(self, message):
        judgement_prompt = Prompt(
            source="User Request From Chat",
            message=message,
            response_format=p.JUDGEMENT_RESPONSE_FORMAT,
        )
        judgement = self._generate_completion(
            new_message=judgement_prompt.generate_prompt()
        )
        return judgement


    async def _determine_mission_objectives(self, message):
        pass


    def _extract_judgement(self, input_text):
        match = re.search(r'\[Judgement\]\n(allow|deny)', input_text)
        
        if match:
            return match.group(1).strip().lower()
        else:
            return 'deny'
        
    def _extract_status(self, input_text):
        match = re.search(r'\[Status\]\n(complete|incomplete|error)', input_text)
        
        if match:
            return match.group(1).strip().lower()
        else:
            return 'error'

app_instance = None

app = FastAPI()

@app.post("/toggle_processing")
async def toggle_processing():
    app_instance.process_messages = not app_instance.process_messages
    return {"detail": f"Message processing set to {app_instance.process_messages}"}

def run_api():
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":

    app_instance = Layer1Aspirant(settings)
    
    if settings.debug:
        run_api()

    app_instance.run()