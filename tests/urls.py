from django.contrib import admin
from django.http import JsonResponse, StreamingHttpResponse
from django.urls import path

from llm_ledger import llm


async def async_answer(request):
    result = await llm.acomplete(request.GET["q"], feature="async_view")
    return JsonResponse({"text": result.text, "request_id": str(result.request_id)})


async def async_stream(request):
    stream = await llm.astream(request.GET["q"], feature="async_stream")

    async def body():
        async with stream:
            async for chunk in stream:
                yield chunk

    return StreamingHttpResponse(body(), content_type="text/plain")


urlpatterns = [
    path("admin/", admin.site.urls),
    path("async/answer/", async_answer),
    path("async/stream/", async_stream),
]
