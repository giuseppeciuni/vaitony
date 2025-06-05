print("Testing imports...")

try:
    from .views.dashboard_views import dashboard, documents_uploaded
    print("✅ dashboard_views OK")
except Exception as e:
    print(f"❌ dashboard_views ERROR: {e}")

try:
    from .views.project_views import new_project, projects_list, project_details, project
    print("✅ project_views OK")
except Exception as e:
    print(f"❌ project_views ERROR: {e}")

try:
    from .views.document_views import serve_project_file
    print("✅ document_views OK")
except Exception as e:
    print(f"❌ document_views ERROR: {e}")

try:
    from .views.user_views import user_profile, billing_settings
    print("✅ user_views OK")
except Exception as e:
    print(f"❌ user_views ERROR: {e}")

try:
    from .views.ia_engine_views import ia_engine, verify_api_key
    print("✅ ia_engine_views OK")
except Exception as e:
    print(f"❌ ia_engine_views ERROR: {e}")

try:
    from .views.crawling_views import website_crawl, handle_website_crawl, handle_website_crawl_internal
    print("✅ crawling_views OK")
except Exception as e:
    print(f"❌ crawling_views ERROR: {e}")

try:
    from .views.chatbot_views import chatbot_widget, chatbot_widget_js, chatwoot_webhook, create_chatwoot_bot_for_project, toggle_url_inclusion
    print("✅ chatbot_views OK")
except Exception as e:
    print(f"❌ chatbot_views ERROR: {e}")

try:
    from .views.config_views import project_config, project_prompts
    print("✅ config_views OK")
except Exception as e:
    print(f"❌ config_views ERROR: {e}")

print("Import test completed!")