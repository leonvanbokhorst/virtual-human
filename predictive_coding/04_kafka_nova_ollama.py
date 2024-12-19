"""
NOVA - Kafka Implementation POC

This experiment implements a three-layer cognitive architecture. Each layer 
operates at different temporal scales and processing depths.

System Architecture:
-------------------
1. Reactive Layer (50-300ms)
   - Handles immediate responses
   - Minimal processing, quick reflexes

2. Responsive Layer (300-1000ms)
   - Context-aware processing
   - Integrates immediate context

3. Reflective Layer (>1000ms)
   - Learning and adaptation
   - Pattern recognition and long-term learning

Message Flow:
------------
Input → Kafka → [Reactive, Responsive, Reflective] → Kafka → Output

Requirements:
------------
- Docker containers for Kafka and Zookeeper
- confluent-kafka-python client
- Python 3.12+ for async/await support

Docker Setup:
------------
# Remove existing containers if needed
# docker rm -f zookeeper kafka

# Run Zookeeper
docker run -d --name zookeeper \
    -e ZOOKEEPER_CLIENT_PORT=2181 \
    -p 2181:2181 \
    confluentinc/cp-zookeeper:latest

# Run Kafka
docker run -d --name kafka \
    --link zookeeper:zookeeper \
    -p 9092:9092 \
    -e KAFKA_ZOOKEEPER_CONNECT=zookeeper:2181 \
    -e KAFKA_ADVERTISED_LISTENERS=PLAINTEXT://localhost:9092 \
    -e KAFKA_LISTENER_SECURITY_PROTOCOL_MAP=PLAINTEXT:PLAINTEXT \
    -e KAFKA_INTER_BROKER_LISTENER_NAME=PLAINTEXT \
    -e KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR=1 \
    confluentinc/cp-kafka:latest
"""

from confluent_kafka import Producer, Consumer
import json
import time
from typing import Dict, Any, Optional
import asyncio
import logging
from ollama import AsyncClient as OllamaClient


logger = logging.getLogger(__name__)

# Model configuration
MODEL_NAME = 'llama3.2:latest'


class KafkaPublishError(Exception):
    """Raised when there is an error publishing messages to Kafka"""

    pass


class NOVALayerError(Exception):
    """Base exception for NOVA layer errors"""

    pass


def timed_process(func):
    """Decorator to add timing information to layer processing"""

    async def wrapper(self, message: Dict[str, Any], *args, **kwargs) -> Dict[str, Any]:
        start_time = time.time()
        try:
            result = await func(self, message, *args, **kwargs)
            end_time = time.time()

            # If result is already a dict, update it; otherwise create new dict
            if isinstance(result, dict):
                result.update(
                    {
                        "start_time": start_time,
                        "end_time": end_time,
                        "processing_duration": end_time - start_time,
                    }
                )
                return result
            else:
                return {
                    "result": result,
                    "start_time": start_time,
                    "end_time": end_time,
                    "processing_duration": end_time - start_time,
                }
        except Exception as e:
            logger.error(
                "Layer processing failed",
                extra={"layer": self.__class__.__name__, "error": str(e)},
                exc_info=True,
            )
            raise NOVALayerError(f"Layer processing failed: {e}") from e

    return wrapper


class NOVALayer:
    """
    Base class for NOVA processing layers.

    Handles Kafka producer/consumer setup and message publishing.
    Each layer inherits from this to implement specific processing logic.

    Args:
        kafka_config (Dict[str, Any]): Kafka configuration parameters
    """

    def __init__(self, kafka_config: Dict[str, Any]):
        # Producer config should exclude consumer-specific settings
        producer_config = {"bootstrap.servers": kafka_config["bootstrap.servers"]}

        # Consumer config can keep all settings
        consumer_config = kafka_config.copy()

        self.producer = Producer(producer_config)
        self.consumer = Consumer(consumer_config)

    def close(self):
        """
        Properly close Kafka resources.
        Should be called when the layer is no longer needed.
        """
        if self.producer:
            self.producer.flush()  # Ensure all messages are sent

        if self.consumer:
            self.consumer.close()

    def __del__(self):
        """Ensure resources are cleaned up when object is garbage collected"""
        try:
            self.close()
        except:
            # Ignore errors during cleanup in destructor
            pass

    def publish(self, topic: str, message: Dict[str, Any]):
        """
        Non-blocking publish to Kafka topic
        """
        try:
            self.producer.produce(
                topic,
                json.dumps(message).encode("utf-8"),
                callback=self.delivery_report,
            )
            self.producer.poll(0)  # Non-blocking poll for callbacks
        except Exception as e:
            logger.error(
                "Failed to publish message to Kafka",
                extra={
                    "topic": topic,
                    "error": str(e),
                    "layer": self.__class__.__name__,
                },
                exc_info=True,
            )
            raise KafkaPublishError(f"Failed to publish message to Kafka: {e}") from e

    def delivery_report(self, err, msg):
        """Callback for Kafka message delivery confirmation"""
        if err is not None:
            logger.error(
                "Message delivery failed",
                extra={
                    "topic": msg.topic(),
                    "error": str(err),
                    "layer": self.__class__.__name__,
                },
            )
            # Note: Can't raise here as it's a callback
            # Consider implementing a message retry mechanism
        else:
            logger.debug(
                "Message delivered successfully",
                extra={"topic": msg.topic(), "layer": self.__class__.__name__},
            )


class ReactiveLayer(NOVALayer):
    """
    Fast response layer (50-100ms)

    Handles immediate responses with minimal processing.
    - Fastest response time
    - Minimal context consideration
    - Basic pattern matching
    """

    def __init__(self, kafka_config: Dict[str, Any]):
        super().__init__(kafka_config)
        self.ollama = OllamaClient()
        self.system_prompt = """You are a reactive processor that gives IMMEDIATE, VERY SHORT responses.
        Rules:
        1. Respond in 10 words or less
        2. Focus only on immediate action or reaction
        3. No explanations or analysis
        4. Be direct and clear
        5. Use imperative form when appropriate"""

    @timed_process
    async def process(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """Quick processing using Ollama for immediate responses"""
        try:
            content = message.get('content', '')
            response = await self.ollama.chat(
                model=MODEL_NAME,
                messages=[
                    {'role': 'system', 'content': self.system_prompt},
                    {'role': 'user', 'content': content}
                ],
                options={
                    'temperature': 0.3,  # Lower temperature for more focused responses
                    'num_predict': 50,   # Limit response length
                }
            )
            
            return {
                "type": "reactive_response",
                "content": response['message']['content'],
                "source": MODEL_NAME
            }
        except Exception as e:
            logger.error(f"Reactive layer processing failed: {str(e)}")
            raise NOVALayerError(f"Reactive processing failed: {e}")


class ResponsiveLayer(NOVALayer):
    """
    Context-aware layer (100-300ms)

    Processes information with awareness of immediate context.
    - Medium response time
    - Context integration
    - Short-term pattern recognition
    """

    def __init__(self, kafka_config: Dict[str, Any]):
        super().__init__(kafka_config)
        self.ollama = OllamaClient()
        self.system_prompt = """You are a responsive processor that considers immediate 
        context and gives thoughtful, measured responses. Balance between quick response 
        and careful consideration."""
        self.context_history = []

    @timed_process
    async def process(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """Process with context awareness using Ollama"""
        try:
            content = message.get('content', '')
            self.context_history.append(content)
            self.context_history = self.context_history[-5:]
            
            context_prompt = f"Previous context: {' | '.join(self.context_history)}\nCurrent input: {content}"
            
            response = await self.ollama.chat(
                model=MODEL_NAME,
                messages=[
                    {'role': 'system', 'content': self.system_prompt},
                    {'role': 'user', 'content': context_prompt}
                ],
                options={'temperature': 0.7,
                         'num_predict': 100,
                         }
            )
            
            return {
                "type": "responsive_response",
                "content": response['message']['content'],
                "context": self.context_history,
                "source": MODEL_NAME
            }
        except Exception as e:
            logger.error(f"Responsive layer processing failed: {str(e)}")
            raise NOVALayerError(f"Responsive processing failed: {e}")


class ReflectiveLayer(NOVALayer):
    """
    Learning and adaptation layer (300-500ms)

    Handles pattern learning and long-term adaptation.
    - Pattern analysis
    - Learning and adaptation
    - Long-term memory integration
    """

    def __init__(self, kafka_config: Dict[str, Any]):
        super().__init__(kafka_config)
        self.ollama = OllamaClient()
        self.system_prompt = """You are a reflective processor focused on deep analysis, 
        pattern recognition, and learning. Consider long-term implications and generate insights."""
        self.learned_patterns = []

    @timed_process
    async def process(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """Process for deep thinking and pattern analysis using Ollama"""
        try:
            content = message.get('content', '')
            patterns_context = "\n".join(self.learned_patterns[-3:])
            
            analysis_prompt = f"""Analyze this input deeply, considering these previous patterns:
            {patterns_context}
            
            Current input: {content}
            
            Identify new patterns, insights, or learning opportunities."""
            
            response = await self.ollama.chat(
                model=MODEL_NAME,
                messages=[
                    {'role': 'system', 'content': self.system_prompt},
                    {'role': 'user', 'content': analysis_prompt}
                ],
                options={'temperature': 0.7}
            )
            
            self.learned_patterns.append(response['message']['content'])
            
            return {
                "type": "reflective_update",
                "content": response['message']['content'],
                "patterns": self.learned_patterns[-3:],
                "source": MODEL_NAME
            }
        except Exception as e:
            logger.error(f"Reflective layer processing failed: {str(e)}")
            raise NOVALayerError(f"Reflective processing failed: {e}")


class NOVA:
    """
    Main NOVA system orchestrator

    Coordinates the three processing layers and handles message distribution.
    Implements parallel processing using asyncio.
    """

    def __init__(self, kafka_config: Dict[str, Any]):
        self.reactive = ReactiveLayer(kafka_config)
        self.responsive = ResponsiveLayer(kafka_config)
        self.reflective = ReflectiveLayer(kafka_config)

    async def process_message(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """Process message through all layers in parallel"""
        logger.info("Starting parallel processing", extra={"timestamp": time.time()})

        tasks = {
            "reactive": self.reactive.process(message),
            "responsive": self.responsive.process(message),
            "reflective": self.reflective.process(message),
        }

        results = {}
        for name, task in tasks.items():
            try:
                results[name] = await task
            except Exception as e:
                logger.error(f"Error in {name} layer", exc_info=True)
                results[name] = {"type": f"{name}_error", "content": str(e)}

        try:
            for layer in (self.reactive, self.responsive, self.reflective):
                layer.producer.flush()
        except Exception as e:
            logger.error("Failed to flush producers", exc_info=True)
            raise

        logger.info("All processing completed", extra={"timestamp": time.time()})
        return results

    async def close(self):
        """Clean up resources for all layers"""
        for layer in (self.reactive, self.responsive, self.reflective):
            layer.close()

    def __del__(self):
        """Ensure all resources are cleaned up"""
        asyncio.create_task(self.close())


async def main():
    """
    Example usage of the NOVA system with integration.
    
    This demonstrates how each layer processes information differently:
    - Reactive: Quick, instinctive responses
    - Responsive: Context-aware processing
    - Reflective: Deep thinking and pattern analysis
    """
    try:
        # Kafka configuration
        kafka_config = {
            "bootstrap.servers": "localhost:9092",
            "group.id": "nova_group",
            "auto.offset.reset": "earliest",
        }

        # Initialize NOVA
        nova = NOVA(kafka_config)

        # Process a sequence of messages to demonstrate different aspects
        messages = [
            {
                "type": "user_input",
                "content": "I'm feeling quite anxious about my presentation tomorrow.",
                "timestamp": time.time(),
            },
            {
                "type": "user_input",
                "content": "I've been preparing for weeks but still don't feel ready.",
                "timestamp": time.time(),
            },
            {
                "type": "user_input",
                "content": "Maybe I should practice one more time?",
                "timestamp": time.time(),
            }
        ]

        print("\n" + "="*80)
        print("NOVA Cognitive Architecture Demo")
        print("="*80)
        print("\nProcessing messages through three cognitive layers:")
        print("1. REACTIVE  - Fast, instinctive responses (≤10 words, immediate actions)")
        print("2. RESPONSIVE - Context-aware, thoughtful responses (considers recent history)")
        print("3. REFLECTIVE - Deep analysis, pattern recognition (learns from experience)")
        print("="*80)
        
        for i, msg in enumerate(messages, 1):
            print(f"\nMessage {i}: {msg['content']}")
            print("-"*80)
            
            results = await nova.process_message(msg)
            
            print("\n🚀 REACTIVE LAYER (Quick, Instinctive Response):")
            print(f"→ {results['reactive']['content']}")
            
            print("\n🧠 RESPONSIVE LAYER (Context-Aware Processing):")
            print("Context considered:", " → ".join(results['responsive']['context']))
            print(f"Response: {results['responsive']['content']}")
            
            print("\n🔮 REFLECTIVE LAYER (Pattern Analysis & Learning):")
            if results['reflective']['patterns']:
                print("Identified Patterns:")
                for pattern in results['reflective']['patterns']:
                    print(f"• {pattern}")
            print(f"\nInsights: {results['reflective']['content']}")
            
            print("\n" + "="*80)
            await asyncio.sleep(1)  # Pause between messages

    finally:
        # Ensure resources are properly cleaned up
        await nova.close()


if __name__ == "__main__":
    asyncio.run(main())