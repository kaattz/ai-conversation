from typing import Any

from homeassistant.components.conversation import (
    DOMAIN as ENTITY_DOMAIN,
    ConversationEntity as BaseEntity,
    ConversationInput,
    ConversationResult,
    ChatLog,
)
from homeassistant.helpers.network import get_url
from homeassistant.components import media_source
from homeassistant.components.media_player.browse_media import async_process_play_media_url

from . import HassEntry, BasicEntity
from .const import *
from .schemas import *


def _strip_glm_box_tokens(text):
    if not text:
        return text
    return text.replace(GLM_BOX_START, '').replace(GLM_BOX_END, '').strip()


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities):
    """Set up conversation entities."""
    for subentry_id, subentry in config_entry.subentries.items():
        if subentry.subentry_type != "conversation":
            continue
        entry = await HassEntry.async_init(hass, config_entry)
        async_add_entities(
            [ConversationEntity(entry, subentry)],
            config_subentry_id=subentry_id,
        )

class ConversationEntity(BasicEntity, BaseEntity):
    """Represent a conversation entity."""
    domain = ENTITY_DOMAIN

    def on_init(self):
        self._attr_unique_id = self.subentry.subentry_id

    @property
    def supported_languages(self):
        """Return a list of supported languages."""
        return MATCH_ALL

    async def _async_handle_message(
        self,
        user_input: ConversationInput,
        chat_log: ChatLog,
    ) -> ConversationResult:
        """Call the API."""
        options = self.subentry.data
        
        # 记录对话输入信息
        LOGGER.debug('Conversation Input - Text: %s', user_input.text)
        LOGGER.debug('Conversation Input - Model: %s', self.model)
        
        try:
            await chat_log.async_provide_llm_data(
                user_input.as_llm_context(DOMAIN),
                options.get(CONF_LLM_HASS_API),
                options.get(CONF_PROMPT),
                user_input.extra_system_prompt,
            )
        except conversation.ConverseError as err:
            LOGGER.error('Conversation Error: %s', err)
            return err.as_conversation_result()

        await self._async_handle_chat_log(chat_log)
        result = conversation.async_get_result_from_chat_log(user_input, chat_log)
        
        # 记录对话结果
        LOGGER.debug('Conversation Result - Response: %s', result.response)
        if hasattr(result, 'error') and result.error:
            LOGGER.error('Conversation Result Error: %s', result.error)
        
        return result

    async def async_explain_media(self, prompt='', image=None, video=None, tags=None, thinking=None, stop=None, **kwargs):
        url = video or image
        if not url:
            return {'error': 'no url'}
        if media_source.is_media_source_id(url):
            media = await media_source.async_resolve_media(self.hass, url, None)
            url = media.url
        if not url.startswith('http'):
            url = async_process_play_media_url(self.hass, url)
        if not url.startswith('http') and video:
            return {'error': f'url error: {url}'}
        internal = get_url(self.hass, prefer_external=False)
        external = get_url(self.hass, prefer_external=True)
        url = url.replace(internal, external)
        if not prompt:
            prompt = 'Analyze and summarize.'
        json_mode = not not tags
        if json_mode:
            prompt += '''
            Please ensure that the response is in JSON schema:
            {
              "message": "string(Summary content, language: $lang)",
              "tags": ["Only return the matched tags ($tags)"]
            }
            '''.strip()
            tags = '|'.join(tags) if isinstance(tags, list) else str(tags)
            prompt = prompt.replace('$tags', tags)
            prompt = prompt.replace('$lang', self.hass.config.language or 'en')
        content = [{'type': 'text', 'text': prompt}]
        if video:
            content.append({'type': 'video_url', 'video_url': {'url': url}})
        else:
            content.append({'type': 'image_url', 'image_url': {'url': url}})
        if not (system_prompt := self.subentry.data.get(CONF_PROMPT)):
            system_prompt = f'Reply in the specified language ({self.hass.config.language}).'
        
        # 根据模型类型决定是否使用thinking参数
        should_use_thinking = thinking and thinking != "" and (
            self.model.startswith("glm-4.5") or
            self.model.startswith("glm-4.6v")
        )

        stop_value = None
        if stop not in (None, ""):
            stop_value_list: list[str]
            if isinstance(stop, (list, tuple)):
                if len(stop) != 1:
                    raise HomeAssistantError("stop 参数只支持单个停止词。")
                stop_value_list = [str(stop[0])]
            elif isinstance(stop, str):
                stop_value_list = [stop]
            else:
                stop_value_list = [str(stop)]
            stop_value = stop_value_list
        
        # 记录调试信息
        LOGGER.debug('Media Analysis - Model: %s, Thinking: %s, Should Use: %s',
                   self.model, thinking, should_use_thinking)
        LOGGER.debug('Media Analysis - Stop: %s', stop_value)
        
        try:
            result = await self.async_chat_completions([
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': content},
            ], thinking=thinking if should_use_thinking else None, stop=stop_value)
        except AIConversationAPIError as err:
            LOGGER.error(
                'Media Analysis - API Error (code=%s): %s',
                err.error_code,
                err,
            )
            return self._build_media_error_response(
                url=url,
                message=str(err),
                error_code=err.error_code,
                raw_error=err.payload or {},
            )
        except HomeAssistantError as err:
            LOGGER.error('Media Analysis - Request Error: %s', err)
            return self._build_media_error_response(
                url=url,
                message=str(err),
            )
       
        # 记录媒体分析结果
        LOGGER.debug('Media Analysis Result - URL: %s', url)
        LOGGER.debug('Media Analysis Result - Raw Message: %s', result.message)
        
        res = {'url': url}
        tags = res.setdefault('tags', [])
        message = result.message
        msg = _strip_glm_box_tokens(message.content if message else '')
        
        if json_mode:
            arr = msg.split('```json')
            try:
                jss = str(arr[1].split('```')[0] if len(arr) > 1 else arr[0])
                dat = json.loads(jss.strip() or '{}')
                msg = dat.get('message', '')
                tags.extend(dat.get('tags', []))
                res['tags_string'] = ' '.join(map(lambda x: f'#{x}', tags))
                LOGGER.debug('Media Analysis - Parsed JSON: %s', dat)
            except Exception as exc:
                res['error'] = str(exc)
                res['result'] = result.to_dict()
                LOGGER.error('Media Analysis - JSON Parse Error: %s', exc)
        
        res['message'] = msg
        if message and 'reasoning_content' in message:
            reasoning_text = _strip_glm_box_tokens(message.reasoning_content)
            res['reasoning'] = reasoning_text
            LOGGER.debug('Media Analysis - Reasoning Content: %s', reasoning_text)
        
        res['usage'] = result.usage
        LOGGER.debug('Media Analysis - Final Result: %s', res)
        return res

    def _build_media_error_response(
        self,
        *,
        url: str,
        message: str,
        error_code: str | None = None,
        raw_error: dict | None = None,
    ) -> dict:
        """Standardize the error payload returned to automations."""
        response: dict[str, Any] = {
            'url': url,
            'message': message,
            'tags': [],
            'error': 'api_error',
            'usage': None,
        }
        if error_code:
            response['error_code'] = error_code
            if error_code == "1113":
                response['error'] = 'insufficient_balance'
        if raw_error:
            response['result'] = {'error': raw_error}
        LOGGER.debug('Media Analysis - Error Response: %s', response)
        return response
